<#
.SYNOPSIS
  Start the AutoOS LiteLLM fallback proxy (:4000) with its keys loaded.

.DESCRIPTION
  litellm does not read configuration/litellm/.env by itself: it resolves
  os.environ/* strictly from the process environment. A proxy started by hand
  (or from the wrong directory) therefore serves every leg as "Missing
  credentials" even when configuration/litellm/.env is complete — measured
  2026-09-22 (META_API_KEY / COHERE_API_KEY legs failing with a full .env).

  This starter reads configuration/litellm/.env (git-ignored, never printed,
  never committed), exports each entry into this process only, sets
  PYTHONUTF8=1 (the banner crashes startup under cp1252), and launches the
  proxy detached. Re-running it restarts the proxy (stale-env convergence).

  Key sources, in order: configuration/api-keys.yml --(mirror)-->
  configuration/litellm/.env --(this script)--> proxy process env.
  Refresh the middle step with: python3 tools/mirror-litellm-env.py

  Env overrides: AUTOOS_LITELLM_HOST (bind address, default 127.0.0.1),
  AUTOOS_LITELLM_PORT (default 4000), AUTOOS_LITELLM_STATE_DIR (where the
  log lives) and AUTOOS_LITELLM_MASTER_KEY_FILE (a private file whose single
  line overrides any LITELLM_MASTER_KEY from .env; unreadable or empty is a
  hard error). Values are never printed, only key names.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$LitDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $LitDir '.env'
$BindHost = if ($env:AUTOOS_LITELLM_HOST) { $env:AUTOOS_LITELLM_HOST } else { '127.0.0.1' }
$Port = if ($env:AUTOOS_LITELLM_PORT) { $env:AUTOOS_LITELLM_PORT } else { '4000' }
$StateDir = if ($env:AUTOOS_LITELLM_STATE_DIR) { $env:AUTOOS_LITELLM_STATE_DIR } else { Join-Path $env:LOCALAPPDATA 'autoos' }

if (-not (Test-Path $EnvFile)) {
    Write-Host "No .env in $LitDir - copy .env.example first (docs/api-keys.md), then mirror keys:"
    Write-Host '  python3 tools/mirror-litellm-env.py'
    exit 1
}

# Export .env entries into THIS process only. Values are never printed:
# only the key names are reported.
$loaded = @()
foreach ($line in (Get-Content $EnvFile -Encoding utf8)) {
    $t = $line.Trim()
    if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $name, $value = $t.Split('=', 2)
    $name = $name.Trim()
    $value = $value.Trim().Trim('"').Trim("'")
    if ($name -eq '' -or $value -eq '' -or $value -match 'REPLACE') { continue }
    Set-Item -Path "Env:$name" -Value $value
    $loaded += $name
}

# An operator can keep the master key out of .env: point
# AUTOOS_LITELLM_MASTER_KEY_FILE at a private file holding only the key. It
# overrides any LITELLM_MASTER_KEY from .env. The value is never printed.
$keyFile = $env:AUTOOS_LITELLM_MASTER_KEY_FILE
if ($keyFile) {
    $master = $null
    if (Test-Path -LiteralPath $keyFile -PathType Leaf) {
        $master = Get-Content -LiteralPath $keyFile -Raw -Encoding utf8 -ErrorAction SilentlyContinue
    }
    if ($null -eq $master) {
        Write-Host "AUTOOS_LITELLM_MASTER_KEY_FILE is set but not a readable file: $keyFile"
        exit 1
    }
    $master = $master.Trim()
    if ($master -eq '') {
        Write-Host "AUTOOS_LITELLM_MASTER_KEY_FILE is set but the file is empty: $keyFile"
        exit 1
    }
    Set-Item -Path 'Env:LITELLM_MASTER_KEY' -Value $master
    if ($loaded -notcontains 'LITELLM_MASTER_KEY') { $loaded += 'LITELLM_MASTER_KEY' }
}
Write-Host ("Keys loaded into proxy env: {0}" -f ($loaded -join ', '))

$env:PYTHONUTF8 = '1'

$stale = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'litellm(\.exe)?[" ]' -and $_.CommandLine -match ('--port ' + [regex]::Escape([string]$Port)) }
foreach ($p in $stale) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch { }
}
if ($stale) { Start-Sleep 3 }

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$Log = Join-Path $StateDir 'litellm.log'
Start-Process -FilePath 'litellm' -ArgumentList '--config', 'config.yaml', '--host', $BindHost, '--port', "$Port" `
    -WorkingDirectory $LitDir -WindowStyle Hidden `
    -RedirectStandardOutput $Log -RedirectStandardError "$Log.err"
Write-Host "litellm start issued from configuration/litellm (log: $Log)"

$up = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep 5
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/" -f $Port) -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
}
if (-not $up) { Write-Host "Proxy did not answer on :$Port - run litellm in a console to see the error."; exit 1 }
Write-Host "LiteLLM proxy up on http://127.0.0.1:$Port"
