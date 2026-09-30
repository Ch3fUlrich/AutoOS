# debug-single.ps1 — single request to debug the 400 error
$ErrorActionPreference = 'Continue'
$Gateway = 'http://127.0.0.1:20128'
$Key = $env:AUTOOS_OMNIROUTE_KEY

# Try listing models
Write-Host "=== /v1/models ==="
try {
  $resp = Invoke-WebRequest -Uri "$Gateway/v1/models" -Headers @{ 'Authorization' = "Bearer $Key" } -UseBasicParsing -TimeoutSec 10
  $models = $resp.Content | ConvertFrom-Json
  Write-Host "Status: $($resp.StatusCode)"
  Write-Host "Models count: $($models.data.Count)"
  $models.data | Select-Object -First 10 | ForEach-Object { Write-Host "  id=$($_.id)" }
} catch {
  Write-Host "Models error: $($_.Exception.Message)"
  if ($_.ErrorDetails -and $_.ErrorDetails.Message) { Write-Host "Details: $($_.ErrorDetails.Message)" }
}

# Try a simple single-message request
Write-Host "`n=== simple request (model=t2) ==="
$simpleBody = @{ model='t2'; messages=@(@{role='user';content='Say hello'}); max_tokens=10; stream=$false } | ConvertTo-Json -Depth 5
try {
  $resp = Invoke-WebRequest -Uri "$Gateway/v1/chat/completions" -Method Post -Headers @{ 'Authorization' = "Bearer $Key"; 'Content-Type' = 'application/json' } -Body $simpleBody -UseBasicParsing -TimeoutSec 30
  Write-Host "Status: $($resp.StatusCode)"
  Write-Host "Response: $($resp.Content.Substring(0, [Math]::Min(300, $resp.Content.Length)))"
} catch {
  Write-Host "Error: $($_.Exception.Message)"
  if ($_.ErrorDetails -and $_.ErrorDetails.Message) { Write-Host "Details: $($_.ErrorDetails.Message)" }
}

# Try with a known model from the models list
Write-Host "`n=== simple request (model from list) ==="
# Try first model from the list
if ($models -and $models.data -and $models.data.Count -gt 0) {
  $firstModel = $models.data[0].id
  Write-Host "Using model: $firstModel"
  $simpleBody2 = @{ model=$firstModel; messages=@(@{role='user';content='Say hello'}); max_tokens=10; stream=$false } | ConvertTo-Json -Depth 5
  try {
    $resp = Invoke-WebRequest -Uri "$Gateway/v1/chat/completions" -Method Post -Headers @{ 'Authorization' = "Bearer $Key"; 'Content-Type' = 'application/json' } -Body $simpleBody2 -UseBasicParsing -TimeoutSec 30
    Write-Host "Status: $($resp.StatusCode)"
    Write-Host "Response: $($resp.Content.Substring(0, [Math]::Min(300, $resp.Content.Length)))"
  } catch {
    Write-Host "Error: $($_.Exception.Message)"
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { Write-Host "Details: $($_.ErrorDetails.Message)" }
  }
}
