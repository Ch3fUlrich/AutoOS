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
$Key = $env:AUTOOS_OMNIROUTE_KEY
if ([string]::IsNullOrWhiteSpace($Key)) {
    # Fall back to the single source of truth for keys.
    $keysFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'configuration\api-keys.yml'
    if (Test-Path $keysFile) {
        foreach ($line in (Get-Content $keysFile -Encoding utf8)) {
            $t = $line.Trim()
            if ($t -match '^omniroute\s*:\s*(.+)$') { $Key = $matches[1].Trim().Trim('"').Trim("'") ; break }
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Key)) {
    Write-Host 'No OmniRoute client key. Add `omniroute: sk-...` to configuration\api-keys.yml,'
    Write-Host 'or set $env:AUTOOS_OMNIROUTE_KEY. Then configure providers: .\configuration\omniroute\apply.ps1'
    exit 1
}
# Export so the launched apps inherit it: opencode.jsonc and the Zed settings
# carry no key by design ("key via env"), so without this the apps the script
# launches would start unauthenticated.
$env:AUTOOS_OMNIROUTE_KEY = $Key

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
        # Stale user settings break the current image (measured 2026-09-21:
        # a persisted schema_version 6 while the image supports 4 -> /api
        # settings 500; meanwhile a live version-3 file serves fine, so only
        # versions NEWER than the image (6+) and unparseable files move
        # aside - with a backup, never a delete. OpenHands regenerates.
        $ohSettings = Join-Path $env:USERPROFILE '.openhands\settings.json'
        if (Test-Path $ohSettings) {
            try { $ohVer = (Get-Content $ohSettings -Raw -Encoding utf8 | ConvertFrom-Json).schema_version }
            catch { $ohVer = 'unparseable' }
            $ohNum = 0
            if ("$ohVer" -match '^\d+$') { $ohNum = [int]"$ohVer" }
            if ($ohVer -eq 'unparseable' -or $ohNum -ge 6) {
                $backup = "$ohSettings.autoos-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
                Copy-Item $ohSettings $backup -Force
                Remove-Item $ohSettings -Force
                Write-Host "Stale OpenHands settings (schema_version $ohVer) moved aside to $backup."
            }
        }
        $existing = (& docker ps -a --format '{{.Names}}' 2>$null) -join "`n"
        if ($existing -match '(?m)^openhands-app$') {
            $running = (& docker ps --format '{{.Names}}' 2>$null) -join "`n"
            if ($running -notmatch '(?m)^openhands-app$') { & docker start openhands-app | Out-Null }
        } else {
            $env:LLM_API_KEY = $Key
            # Detached, no -it: -it fails without a TTY (non-interactive shells)
            # and foreground -it never returns, so the URL line below would lie.
            & docker run -d --rm `
                -e LLM_MODEL=openai/tier1 `
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
            Write-Host 'Starting opencode serve in the background...'
            Start-Process -FilePath 'opencode' -ArgumentList 'serve', '--hostname', '0.0.0.0', '--port', '4096' -WindowStyle Hidden
            Write-Host 'opencode serve should answer on http://localhost:4096 (401 = alive, pair via: opencode pair).'
        }
    }
}
