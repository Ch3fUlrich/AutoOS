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
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$LitDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $LitDir '.env'

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
Write-Host ("Keys loaded into proxy env: {0}" -f ($loaded -join ', '))

$env:PYTHONUTF8 = '1'

$stale = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'litellm(\.exe)?[" ]' -and $_.CommandLine -match '--port 4000' }
foreach ($p in $stale) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch { }
}
if ($stale) { Start-Sleep 3 }

Start-Process -FilePath 'litellm' -ArgumentList '--config', 'config.yaml', '--port', '4000' `
    -WorkingDirectory $LitDir -WindowStyle Hidden
Write-Host 'litellm start issued from configuration/litellm'

$up = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep 5
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:4000/' -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
}
if (-not $up) { Write-Host 'Proxy did not answer on :4000 - run `litellm` in a console to see the error.'; exit 1 }
Write-Host 'LiteLLM proxy up on http://127.0.0.1:4000'
