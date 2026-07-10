param()

Write-Host "[tools] Installing command-line tools (fzf, ripgrep)..." -ForegroundColor Green
function Test-Command($cmd) { [bool](Get-Command -Name $cmd -ErrorAction SilentlyContinue) }

$tools = @(
    @{Name='junegunn.fzf'; Cmd='fzf'; Description='FZF - command-line fuzzy finder'},
    @{Name='BurntSushi.ripgrep.MSVC'; Cmd='rg'; Description='ripgrep - fast recursive grep'}
)

$jobs = @()
foreach ($t in $tools) {
    if (-not (Test-Command $t.Cmd)) {
        Write-Host "Starting installation for $($t.Description) ($($t.Name))..." -ForegroundColor Green
        $job = Start-Job -ScriptBlock {
            param($name)
            winget install $name --source winget --scope user --force --accept-package-agreements --accept-source-agreements
            return $LASTEXITCODE
        } -ArgumentList $t.Name
        $jobs += @{ Tool = $t; Job = $job }
    } else {
        Write-Host "$($t.Description) present." -ForegroundColor Green
    }
}

foreach ($item in $jobs) {
    $job = $item.Job
    $t = $item.Tool
    Wait-Job $job | Out-Null
    $output = Receive-Job $job

    $exitCode = 0
    $jobHasErrors = $job.ChildJobs[0].JobStateInfo.State -eq 'Failed' -or $job.ChildJobs[0].Error.Count -gt 0
    if ($null -ne $output) {
        if ($output -is [array] -and $output.Count -gt 0) {
            $exitCode = $output[-1]
            if ($output.Count -gt 1) {
                $output[0..($output.Count-2)]
            }
        } else {
            $exitCode = $output
        }
    }

    if ($exitCode -ne 0 -or $jobHasErrors) {
        Write-Host "winget failed for $($t.Name)" -ForegroundColor Yellow
    }
    if (-not (Test-Command $t.Cmd)) {
        $env:PATH += ";$env:LOCALAPPDATA\Microsoft\WinGet\Links"
        Start-Sleep -Milliseconds 200
    }
}

Write-Host "Tools installation step finished." -ForegroundColor Cyan
