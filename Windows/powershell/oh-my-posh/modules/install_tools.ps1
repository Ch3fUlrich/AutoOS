param()

Write-Host "[tools] Installing command-line tools (fzf, ripgrep)..." -ForegroundColor Green
function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }

$tools = @(
    @{Name='junegunn.fzf'; Cmd='fzf'; Description='FZF - command-line fuzzy finder'},
    @{Name='BurntSushi.ripgrep.MSVC'; Cmd='rg'; Description='ripgrep - fast recursive grep'}
)

foreach ($t in $tools) {
    if (-not (Test-Command $t.Cmd)) {
        Write-Host "Installing $($t.Description) ($($t.Name))..." -ForegroundColor Green
        winget install $t.Name --source winget --scope user --force --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Write-Host "winget failed for $($t.Name)" -ForegroundColor Yellow }
        if (-not (Test-Command $t.Cmd)) { $env:PATH += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"; Start-Sleep -Milliseconds 200 }
    } else {
        Write-Host "$($t.Description) present." -ForegroundColor Green
    }
}

Write-Host "Tools installation step finished." -ForegroundColor Cyan
