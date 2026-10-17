#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1') -DisableNameChecking

$script:Progress = $null
$script:Started = $null

function Start-AutoOSComponentProgress {
    param([string]$Id, [string]$Name, [int]$Done, [int]$Total)
    $script:Started = [Diagnostics.Stopwatch]::StartNew()
    $script:Progress = [ordered]@{
        done = $Done; total = $Total; id = $Id; name = $Name
        phase = 'checking'; percent = $null; elapsedSeconds = 0; status = 'running'
    }
    Write-AutoOSInstallProgress
}

function Write-AutoOSInstallProgress {
    param([string]$Phase, [int]$Percent = -1, [switch]$Complete)
    if ($null -eq $script:Progress) { return }
    if ($Phase) { $script:Progress.phase = $Phase }
    $script:Progress.percent = if ($Percent -ge 0) { [Math]::Min(100, $Percent) } else { $null }
    $script:Progress.elapsedSeconds = [int]$script:Started.Elapsed.TotalSeconds
    if ($Complete) {
        $script:Progress.done++
        $script:Progress.status = 'complete'
        $script:Progress.percent = if ($Phase -in @('installed', 'skipped')) { 100 } else { $null }
    }
    if ($env:AUTOOS_PROGRESS_EVENTS -eq '1') {
        Write-AutoOSLine ('@@AUTOOS_PROGRESS ' + ($script:Progress | ConvertTo-Json -Compress))
        return
    }
    $overall = if ($script:Progress.total) { [int][Math]::Floor(100 * $script:Progress.done / $script:Progress.total) } else { 0 }
    $filled = [int][Math]::Floor($overall / 5)
    Write-AutoOSLine ('Overall [{0}{1}] {2}/{3} ({4}%)' -f ('#' * $filled), ('-' * (20 - $filled)), $script:Progress.done, $script:Progress.total, $overall)
    $current = if ($null -eq $script:Progress.percent) { '[     working      ] percentage unavailable' } else {
        $count = [int][Math]::Floor($script:Progress.percent / 5)
        '[{0}{1}] {2}%' -f ('#' * $count), ('-' * (20 - $count)), $script:Progress.percent
    }
    Write-AutoOSLine ('Current {0}: {1} - {2} ({3}s)' -f $script:Progress.name, $current, $script:Progress.phase, $script:Progress.elapsedSeconds)
}

Export-ModuleMember -Function Start-AutoOSComponentProgress, Write-AutoOSInstallProgress
