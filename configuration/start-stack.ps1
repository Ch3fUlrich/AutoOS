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

function Write-AutoOSLine {
    # Standalone stand-in for the module's console writer (AutoOS.Ui.psm1): this script imports no
    # module. Same call shape (-Level). The gateway-key functions below are word for word the
    # module's, so the copy cannot drift behind a text substitution.
    param(
        [Parameter(Position = 0)][string]$Message = '',
        [ValidateSet('plain', 'info', 'ok', 'warn', 'error', 'step', 'muted', 'head')]
        [string]$Level = 'plain'
    )
    switch ($Level) {
        'error' { Write-Host $Message -ForegroundColor Red }
        'warn'  { Write-Host $Message -ForegroundColor Yellow }
        'ok'    { Write-Host $Message -ForegroundColor Green }
        'muted' { Write-Host $Message -ForegroundColor DarkGray }
        default { Write-Host $Message }
    }
}

function ConvertFrom-AutoOSKeyValue {
    # Mirrors tools/keys_file.py parse_value: a quoted value is what sits between the quotes,
    # an unquoted one is cut at the first space-or-tab followed by '#', then right-trimmed.
    param([string]$Raw)
    if (-not $Raw) { return '' }
    $first = $Raw[0]
    if ($first -eq '"' -or $first -eq "'") {
        $end = $Raw.IndexOf($first, 1)
        if ($end -gt 0) { return $Raw.Substring(1, $end - 1) }
        return $Raw.Substring(1)
    }
    $cut = -1
    for ($k = 0; $k -lt $Raw.Length - 1; $k++) {
        if (($Raw[$k] -eq ' ' -or $Raw[$k] -eq [char]9) -and $Raw[$k + 1] -eq '#') { $cut = $k; break }
    }
    if ($cut -ge 0) { $Raw = $Raw.Substring(0, $cut + 1) }
    return $Raw.TrimEnd()
}

function Read-AutoOSKeyMap {
    # Mirrors tools/keys_file.py read_keys: name=value and name: value, names keep their case,
    # empty values and values containing REPLACE are skipped, the first filled-in value wins.
    param([string]$Path)
    $map = New-Object 'System.Collections.Generic.Dictionary[string,string]'
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($raw in (Get-Content -LiteralPath $Path -Encoding utf8)) {
        $row = $raw.Trim()
        if ($row.Length -eq 0 -or $row.StartsWith('#')) { continue }
        $eq = $row.IndexOf('='); $colon = $row.IndexOf(':')
        if ($eq -lt 0 -and $colon -lt 0) { continue }
        $cut = if ($colon -lt 0) { $eq } elseif ($eq -lt 0) { $colon } else { [Math]::Min($eq, $colon) }
        $name = $row.Substring(0, $cut).Trim()
        $rawVal = $row.Substring($cut + 1).Trim()
        if (-not $name -or -not $rawVal) { continue }
        $value = ConvertFrom-AutoOSKeyValue $rawVal
        if ($value -and -not $value.Contains('REPLACE') -and -not $map.ContainsKey($name)) {
            $map[$name] = $value
        }
    }
    return $map
}

function Get-AutoOSKeyValue {
    # Exact-name lookup, like `tools/keys_file.py <file> <name>`. Returns '' when the file or key is missing.
    param([string]$Path, [string]$Name)
    $map = Read-AutoOSKeyMap $Path
    if ($Name -and $map.ContainsKey($Name)) { return $map[$Name] }
    return ''
}

function Find-AutoOSKey {
    # Case-insensitive lookup, like tools/autoos_gateway_key.py resolve_client_key.
    param($Map, [string]$Name)
    foreach ($k in $Map.Keys) { if ($k -ieq $Name) { return $Map[$k] } }
    return ''
}

function Write-AutoOSNoticeOnce {
    # A notice prints once per session, like the Python resolver, even when two callers resolve.
    param([string]$Message)
    if (-not (Get-Variable -Name AutoOSNoticed -Scope Script -ErrorAction SilentlyContinue)) { $script:AutoOSNoticed = @{} }
    if ($script:AutoOSNoticed.ContainsKey($Message)) { return }
    $script:AutoOSNoticed[$Message] = $true
    Write-AutoOSLine $Message
}

# ─── OmniRoute gateway key resolution (mirrors lib/linux/install.sh) ───
# This is a COPY of Test-AutoOSLocalGateway from lib/windows/AutoOS.Install.psm1.
# If the logic changes, update both. A parity test in tests/run-tests.ps1 asserts
# they give the same answers for the WS-OMNIREMOTE URL table.
function Test-AutoOSLocalGateway {
    # The same rule as tools/autoos_gateway_key.py is_local_gateway (see its docstring): a plain
    # string parse, not [Uri], because [Uri] and urlparse read odd URLs differently and a local key
    # must never reach a remote gateway because they disagreed.
    param([string]$Url)
    if ([string]::IsNullOrEmpty($Url)) { return $true }
    $Url = $Url.Trim([char[]]@(32, 9, 10, 11, 12, 13))
    if ($Url.Length -eq 0) { return $false }
    foreach ($ch in $Url.ToCharArray()) {
        $code = [int]$ch
        if ($code -lt 0x21 -or $code -gt 0x7E -or $code -eq 92) { return $false }
    }
    $sep = $Url.IndexOf('://')
    if ($sep -lt 0) { return $false }
    $scheme = $Url.Substring(0, $sep)
    # only http and https name a gateway: file://127.0.0.1 and ftp://127.0.0.1 are not one
    if ($scheme -ne 'http' -and $scheme -ne 'https') { return $false }
    $rest = $Url.Substring($sep + 3)
    $cut = $rest.IndexOfAny([char[]]@('/', '?', '#'))
    if ($cut -ge 0) { $rest = $rest.Substring(0, $cut) }
    $at = $rest.LastIndexOf('@')
    $authority = if ($at -ge 0) { $rest.Substring($at + 1) } else { $rest }
    $bracketed = $false
    if ($authority.StartsWith('[')) {
        $end = $authority.IndexOf(']')
        if ($end -lt 0) { return $false }
        $gwHost = $authority.Substring(1, $end - 1)
        $tail = $authority.Substring($end + 1)
        $bracketed = $true
    } else {
        $colon = $authority.IndexOf(':')
        if ($colon -lt 0) { $gwHost = $authority; $tail = '' }
        else {
            $gwHost = $authority.Substring(0, $colon)
            $tail = $authority.Substring($colon)
            if ($tail.Substring(1).Contains(':')) { return $false }
        }
    }
    if ($tail.Length -gt 0) {
        if (-not $tail.StartsWith(':')) { return $false }
        $port = $tail.Substring(1)
        if ($port.Length -gt 0) {
            if ($port.Length -gt 5 -or $port -notmatch '^[0-9]+$' -or [int]$port -gt 65535) { return $false }
        }
    }
    $gwHost = $gwHost.ToLowerInvariant()
    if ($bracketed) { return ($gwHost -eq '::1') }
    return ($gwHost -eq '127.0.0.1' -or $gwHost -eq 'localhost')
}

function Get-AutoOSHostConfigPath {
    if ($env:AUTOOS_HOST_CONFIG) {
        $path = [Environment]::ExpandEnvironmentVariables($env:AUTOOS_HOST_CONFIG)
        # Expand leading ~ (parity with Python/bash)
        if ($path -like '~*') {
            $path = $env:USERPROFILE + $path.Substring(1)  # literal: a $ in the profile path is not a replacement token
        }
        return $path
    }
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
            if ($line -cmatch '^host_name\s*:\s*(.+)$') {
                $v = $Matches[1].Trim().Trim('"',"'")
                if ($v) { return ConvertTo-AutoOSHostName $v }
            }
        }
    }
    try { $fqdn = [System.Net.Dns]::GetHostName() } catch { $fqdn = 'localhost' }
    $normalized = ConvertTo-AutoOSHostName $fqdn
    Write-AutoOSNoticeOnce "AutoOS: using hostname '$normalized' for omniroute key field (set AUTOOS_HOST_NAME or host_name in $hostFile to override)"
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
    # -Optional: a missing key returns $null without a message (callers that treat the key as optional)
    param([string]$KeysFile, [switch]$Optional)
    # 1. Explicit env always wins
    if (-not [string]::IsNullOrWhiteSpace($env:AUTOOS_OMNIROUTE_KEY)) {
        return $env:AUTOOS_OMNIROUTE_KEY
    }
    $field = Get-AutoOSClientKeyField
    $isLocal = Test-AutoOSLocalGateway $env:AUTOOS_OMNIROUTE_URL
    $map = Read-AutoOSKeyMap $KeysFile
    # 2. New field
    $key = Find-AutoOSKey $map $field
    if (-not [string]::IsNullOrWhiteSpace($key)) { return $key }
    # 3. Legacy fallback (one release, read-only)
    $legacyField = if ($isLocal) { 'omniroute' } else { "omniroute_client_$(Get-AutoOSHostName)" }
    $legacyKey = Find-AutoOSKey $map $legacyField
    if (-not [string]::IsNullOrWhiteSpace($legacyKey)) {
        Write-AutoOSNoticeOnce "api-keys.yml: '$legacyField' is deprecated, rename it to '$field'"
        return $legacyKey
    }
    # 4. Missing - clear error
    if ($Optional) { return $null }
    $context = if ($isLocal) { 'a local gateway' } else { 'a non-local gateway' }
    $hostFile = Get-AutoOSHostConfigPath
    Write-AutoOSLine "No OmniRoute client key for $context. Expected field '$field' in $KeysFile (or set AUTOOS_OMNIROUTE_KEY). Host name from AUTOOS_HOST_NAME or $hostFile (host_name:), falling back to short hostname." -Level error
    return $null
}

# BEGIN key-block
# This launcher talks to $Gateway, so THAT url (not a stale AUTOOS_OMNIROUTE_URL left in the environment) decides
# local vs remote: a remote omniroute_server key must never be exported to apps that talk to the local gateway.
# The variable is set around the resolver call only and restored afterwards.
$savedGatewayUrl = $env:AUTOOS_OMNIROUTE_URL
$env:AUTOOS_OMNIROUTE_URL = $Gateway
try { $Key = Get-AutoOSClientKey -KeysFile $keysFile }
finally { if ($null -eq $savedGatewayUrl) { Remove-Item Env:AUTOOS_OMNIROUTE_URL -ErrorAction SilentlyContinue } else { $env:AUTOOS_OMNIROUTE_URL = $savedGatewayUrl } }
if ($null -eq $Key) { exit 1 }
# Export so the launched apps inherit it: opencode.jsonc and the Zed settings
# carry no key by design ("key via env"), so without this the apps the script
# launches would start unauthenticated.
$env:AUTOOS_OMNIROUTE_KEY = $Key
# END key-block

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
    # Sane skip-on-repeated-429 policy (operator 2026-10-01): the gateway reads
    # these from its own process env at startup (open-sse/services/rotationConfig.ts:84-110;
    # provider-breaker family at open-sse/config/constants.ts:251-277), so the
    # launcher that spawns it is the only surface. Rotate a leg only after three
    # 429s inside a 120s window (the shipped default of 1 hops on the first
    # 429), then cool the leg for 300s. Respect-set: never clobber a user value.
    if (-not $env:OMNIROUTE_ROTATION_ENABLED) {
        $env:OMNIROUTE_ROTATION_ENABLED = 'true'
    }
    if (-not $env:OMNIROUTE_ROTATE_ON_429) {
        $env:OMNIROUTE_ROTATE_ON_429 = 'true'
    }
    if (-not $env:OMNIROUTE_ROTATE_429_THRESHOLD) {
        $env:OMNIROUTE_ROTATE_429_THRESHOLD = '3'
    }
    if (-not $env:OMNIROUTE_ROTATE_429_WINDOW_SECONDS) {
        $env:OMNIROUTE_ROTATE_429_WINDOW_SECONDS = '120'
    }
    if (-not $env:OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS) {
        $env:OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS = '300'
    }
    if (-not $env:OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS) {
        $env:OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS = '1800000'
    }
    # Raise the chat admission heavy-in-flight limit from the default of 1.
    # Default 1 + 1 healthy-headroom = 2 max concurrent heavy requests; a 3rd
    # concurrent heavy stream gets 503 chat_admission_busy. 8 gives headroom
    # for parallel agents (swarm, multi-lane) without over-allocating heap.
    # Respect a user-set value — do not clobber.
    if (-not $env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT) {
        $env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT = '8'
    }
    Write-Host 'Starting OmniRoute in the background...'
    # A bare 'omniroute' resolves to the npm .ps1 shim (ExternalScript), which
    # Start-Process cannot launch as a Win32 app - pin the .cmd shim instead.
    # Resolved via PATH with a %APPDATA%\npm fallback (never a hardcoded user
    # path): a missing shim is a loud failure, not a silent wait for a gateway
    # that never starts.
    $omnirouteCmd = $null
    $omnirouteCmdInfo = Get-Command omniroute.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($omnirouteCmdInfo) { $omnirouteCmd = $omnirouteCmdInfo.Source }
    if (-not $omnirouteCmd) { $omnirouteCmd = Join-Path $env:APPDATA 'npm\omniroute.cmd' }
    if (-not (Test-Path -LiteralPath $omnirouteCmd)) {
        Write-Host "Could not find omniroute.cmd (the npm shim). Run: .\setup.ps1 -Only omniroute -Yes"
        exit 1
    }
    Start-Process -FilePath $omnirouteCmd -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden
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
