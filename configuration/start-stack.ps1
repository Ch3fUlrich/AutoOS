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
param([ValidateSet('opencode', 'zed', 'nvim', 'openhands', 'none')][string]$App = 'none')

$ErrorActionPreference = 'Stop'
$Gateway = 'http://127.0.0.1:20128'
$Key = $env:AUTOOS_OMNIROUTE_KEY
if ([string]::IsNullOrWhiteSpace($Key)) {
    Write-Host 'Set $env:AUTOOS_OMNIROUTE_KEY first (OmniRoute dashboard -> api-manager -> Create API Key).'
    exit 1
}

function Test-Gateway {
    try { (Invoke-WebRequest -Uri "$Gateway/v1/models" -Headers @{ Authorization = "Bearer $Key" } -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
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
        # Docker Desktop must be running; container reaches the gateway via host IP.
        docker run -it --rm --pull=always `
            -e LLM_MODEL=auto/smart `
            -e LLM_API_KEY="$Key" `
            -e LLM_BASE_URL="http://host.docker.internal:20128/v1" `
            -e SANDBOX_RUNTIME_CONTAINER_IMAGE=docker.all-hands.dev/all-hands-ai/runtime:latest `
            -e LOG_ALL_EVENTS=true `
            -v /var/run/docker.sock:/var/run/docker.sock `
            -v "$env:USERPROFILE\.openhands:/.openhands" `
            --add-host host.docker.internal:host-gateway `
            --name openhands-app `
            docker.all-hands.dev/all-hands-ai/openhands:latest
    }
}
