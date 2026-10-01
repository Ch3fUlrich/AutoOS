# measure-admission.ps1 — fire N concurrent heavy chat requests, report accept/reject
# Usage: .\measure-admission.ps1 -Concurrency 4 -Label "before"
param(
  [int]$Concurrency = 4,
  [string]$Label = "run",
  [int]$MaxTokens = 200,
  [int]$MessageCount = 210,   # > CHAT_HEAVY_MESSAGE_COUNT (200) to trigger "heavy"
  [string]$Model = "t2-worker",
  [bool]$Stream = $true
)

$ErrorActionPreference = 'Continue'
$Gateway = 'http://127.0.0.1:20128'
$Key = $env:AUTOOS_OMNIROUTE_KEY
if ([string]::IsNullOrWhiteSpace($Key)) {
  # The one rule (env, then omniroute_server / omniroute_<host>, then the legacy field), by the resolver CLI
  $keysFile = Join-Path $PSScriptRoot 'configuration\api-keys.yml'
  $resolver = Join-Path $PSScriptRoot 'tools\autoos_gateway_key.py'
  if ((Test-Path $keysFile) -and (Test-Path $resolver)) {
    $env:AUTOOS_OMNIROUTE_URL = $Gateway
    $Key = ((& python $resolver resolve --optional --no-notice $keysFile) -join '').Trim()
  }
}
if ([string]::IsNullOrWhiteSpace($Key)) {
  Write-Host "ERROR: No API key. Set AUTOOS_OMNIROUTE_KEY or fill configuration/api-keys.yml"
  exit 1
}

# Build a heavy body: 210 short messages → triggers CHAT_HEAVY_MESSAGE_COUNT threshold
$messages = @()
$messages += @{ role = 'system'; content = 'You are a helpful assistant. Count from 1 to 50.' }
for ($i = 1; $i -le $MessageCount - 1; $i++) {
  $messages += @{ role = 'user'; content = "Message number $i. Say hi." }
  $messages += @{ role = 'assistant'; content = "Hi $i." }
}
# Trim to exact count
$messages = $messages[0..($MessageCount - 1)]

$body = @{
  model = $Model
  messages = $messages
  max_tokens = $MaxTokens
  stream = $Stream
} | ConvertTo-Json -Depth 5

$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$bodyKB = [math]::Round($bodyBytes.Length / 1024, 1)
Write-Host "[$Label] concurrency=$Concurrency messages=$MessageCount body=${bodyKB}KB max_tokens=$MaxTokens model=$Model stream=$Stream"
Write-Host "[$Label] OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT env = '$($env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT)'"
$startDate = Get-Date
Write-Host "[$Label] start: $($startDate.ToString('o'))"

$results = @()
$jobs = @()
for ($i = 0; $i -lt $Concurrency; $i++) {
  $idx = $i
  $jobs += Start-Job -ScriptBlock {
    param($Gateway, $Key, $Body, $Idx)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
      $resp = Invoke-WebRequest -Uri "$Gateway/v1/chat/completions" -Method Post `
        -Headers @{ 'Authorization' = "Bearer $Key"; 'Content-Type' = 'application/json' } `
        -Body $Body -UseBasicParsing -TimeoutSec 120
      $sw.Stop()
      $code = $resp.StatusCode
      $content = $resp.Content
      # Extract just the error code if it's an error, or first 200 chars of content
      $snippet = ""
      try {
        $json = $content | ConvertFrom-Json
        if ($json.error) {
          $snippet = "code=$($json.error.code) msg=$($json.error.message)"
        } else {
          $snippet = "ok $($content.Substring(0, [Math]::Min(100, $content.Length)))"
        }
      } catch {
        $snippet = $content.Substring(0, [Math]::Min(200, $content.Length))
      }
      [PSCustomObject]@{ idx=$Idx; status=$code; ms=$sw.ElapsedMilliseconds; snippet=$snippet }
    } catch {
      $sw.Stop()
      $msg = $_.Exception.Message
      $code = 0
      if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
      $snippet = $msg
      if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        try {
          $errJson = $_.ErrorDetails.Message | ConvertFrom-Json
          if ($errJson.error) {
            $snippet = "code=$($errJson.error.code) msg=$($errJson.error.message)"
          }
        } catch { $snippet = $_.ErrorDetails.Message.Substring(0, [Math]::Min(200, $_.ErrorDetails.Message.Length)) }
      }
      [PSCustomObject]@{ idx=$Idx; status=$code; ms=$sw.ElapsedMilliseconds; snippet=$snippet }
    }
  } -ArgumentList $Gateway, $Key, $body, $idx
}

# Wait for all jobs
$jobs | Wait-Job -Timeout 150 | Out-Null
foreach ($j in $jobs) {
  $r = Receive-Job $j
  if ($r) { $results += $r }
}
$jobs | Remove-Job -Force

$endDate = Get-Date
$accepted = ($results | Where-Object { $_.status -eq 200 }).Count
$rejected = ($results | Where-Object { $_.status -ne 200 }).Count
Write-Host "[$Label] end: $($endDate.ToString('o'))"
Write-Host "[$Label] RESULTS: accepted=$accepted rejected=$rejected / total=$Concurrency"
$results | Sort-Object idx | ForEach-Object {
  Write-Host "[$Label]   req[$($_.idx)] status=$($_.status) ms=$($_.ms) | $($_.snippet)"
}
Write-Host "[$Label] SUMMARY: $accepted accepted, $rejected rejected"
