#Requires -Version 5.1
<#
.SYNOPSIS
    AutoOS browser UI for headless or remote Windows machines.

.DESCRIPTION
    Serves web/index.html and drives the real installer by shelling out to
    setup.ps1, so the browser path and the terminal path cannot drift apart.

    Security: binds 127.0.0.1 by default and always requires a per-run token.
    A wider bind is opt-in and warned about, because this endpoint installs
    software. Non-loopback binding also needs an elevated shell (HTTP.sys URL
    reservation), which is called out rather than failing obscurely.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'AutoOS.Ui.psm1')      -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Detect.psm1')  -DisableNameChecking
Import-Module (Join-Path $PSScriptRoot 'AutoOS.Catalog.psm1') -DisableNameChecking

$script:Log     = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
$script:RunInfo = [hashtable]::Synchronized(@{ Running = $false; Done = 0; Total = 0; Summary = '' })

function Get-AutoOSLineLevel {
    param([string]$Line)
    $t = $Line.TrimStart()
    if ($t.StartsWith('+ ')) { return 'ok' }
    if ($t.StartsWith('! ')) { return 'warn' }
    if ($t.StartsWith('x ')) { return 'err' }
    if ($t.StartsWith('> ')) { return 'step' }
    if ($t -match '^(run:|would run:|would )') { return 'muted' }
    ''
}

function Get-AutoOSServeState {
    param([psobject]$SystemInfo, [psobject]$Catalog)

    $available = @(Get-AutoOSAvailableComponents -Catalog $Catalog -SystemInfo $SystemInfo)
    $components = foreach ($c in $available) {
        [ordered]@{
            id = $c.Id; name = $c.Name; description = $c.Description
            provider = $c.Provider; package = $c.Package
            profiles = @($c.Profiles); prompt = $c.Prompt; category = $c.Category
            requires = @($c.Requires); homepage = $c.Homepage
            verify = $c.Verify; notes = $c.Notes
        }
    }
    [ordered]@{
        platform  = 'Windows'
        system    = [ordered]@{
            host           = $env:COMPUTERNAME
            'operating system' = $SystemInfo.OsName
            architecture   = $SystemInfo.Arch
            model          = "$($SystemInfo.Manufacturer) $($SystemInfo.Model)"
            cpu            = $SystemInfo.CpuName
            cores          = "$($SystemInfo.CpuCores)"
            memory         = "$($SystemInfo.RamGB) GB"
            'free disk'    = "$($SystemInfo.FreeDiskGB) GB"
            user           = $SystemInfo.UserName
            elevated       = $(if ($SystemInfo.IsAdmin) { 'yes' } else { 'no' })
        }
        suggested = Get-AutoOSSuggestedProfile -SystemInfo $SystemInfo
        profiles  = $Catalog.profiles
        prompts   = $Catalog.prompts
        components = @($components)
    }
}

function Start-AutoOSInstallJob {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string[]]$Ids,
        [hashtable]$Answers = @{},
        [bool]$DryRun = $true
    )
    $script:Log.Clear()
    $script:RunInfo.Running = $true
    $script:RunInfo.Done    = 0
    $script:RunInfo.Total   = $Ids.Count
    $script:RunInfo.Summary = ''

    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $RepoRoot 'setup.ps1'),
                '-Only', ($Ids -join ','), '-Yes', '-NoColor')
    if ($DryRun) { $psArgs += '-DryRun' }

    [void]$script:Log.Add(@{ level = 'step'; text = '$ powershell ' + ($psArgs -join ' ') })

    $env:AUTOOS_NO_COLOR = '1'
    foreach ($k in $Answers.Keys) {
        if ($Answers[$k]) {
            Set-Item -Path ("Env:AUTOOS_ANSWER_" + ($k.ToUpper() -replace '-', '_')) -Value $Answers[$k]
        }
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = (Get-Command powershell).Source
    $psi.Arguments = ($psArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory      = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true

    $proc = [System.Diagnostics.Process]::Start($psi)

    # Reader thread so the HTTP listener stays responsive while the install runs.
    $reader = [System.Threading.Thread]::new({
        while (-not $proc.StandardOutput.EndOfStream) {
            $line = $proc.StandardOutput.ReadLine()
            if ($null -eq $line) { break }
            [void]$script:Log.Add(@{ level = (Get-AutoOSLineLevel $line); text = $line })
            if ($line.TrimStart().StartsWith('> [')) { $script:RunInfo.Done++ }
        }
        $err = $proc.StandardError.ReadToEnd()
        if ($err) { [void]$script:Log.Add(@{ level = 'err'; text = $err.Trim() }) }
        $proc.WaitForExit()
        $script:RunInfo.Running = $false
        $script:RunInfo.Summary = "finished (exit $($proc.ExitCode))"
        [void]$script:Log.Add(@{
            level = $(if ($proc.ExitCode -eq 0) { 'ok' } else { 'err' })
            text  = "--- exit code $($proc.ExitCode) ---"
        })
    })
    $reader.IsBackground = $true
    $reader.Start()
}

function Start-AutoOSServer {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [int]$Port = 8777,
        [string]$Bind = '127.0.0.1',
        [Parameter(Mandatory)][psobject]$SystemInfo,
        [Parameter(Mandatory)][psobject]$Catalog,
        # Starting the server with -DryRun LOCKS the whole session to dry-run:
        # the browser can then only preview, never install. A safe way to hand
        # someone the URL without handing them the ability to change the box.
        [switch]$DryRun
    )
    $forceDryRun = [bool]$DryRun

    $indexPath = Join-Path $RepoRoot 'web\index.html'
    if (-not (Test-Path $indexPath)) {
        Write-AutoOSLine 'web\index.html is missing - cannot start the browser UI.' -Level error
        return
    }

    $token = [Convert]::ToBase64String([Guid]::NewGuid().ToByteArray()).TrimEnd('=').Replace('/', '_').Replace('+', '-')
    $prefixHost = if ($Bind -eq '127.0.0.1') { 'localhost' } else { '+' }
    $listener = New-Object System.Net.HttpListener
    $listener.Prefixes.Add("http://${prefixHost}:$Port/")

    try {
        $listener.Start()
    } catch {
        Write-AutoOSLine "Could not listen on port $Port : $($_.Exception.Message)" -Level error
        if ($prefixHost -eq '+') {
            Write-AutoOSLine 'Binding beyond localhost needs an elevated shell, or a URL reservation:' -Level muted
            Write-AutoOSLine "    netsh http add urlacl url=http://+:$Port/ user=$env:USERNAME" -Level muted
        }
        return
    }

    $url = "http://$(if ($Bind -eq '127.0.0.1') { 'localhost' } else { $Bind }):$Port/?token=$token"
    Write-AutoOSLine ''
    Write-AutoOSLine '  AutoOS browser UI' -Level head
    Write-AutoOSLine ('  ' + ('-' * 58)) -Level muted
    Write-AutoOSLine "  $url"
    Write-AutoOSLine ('  ' + ('-' * 58)) -Level muted
    if ($Bind -ne '127.0.0.1') {
        Write-AutoOSLine 'WARNING: bound beyond loopback. Anyone who can reach this port AND' -Level warn
        Write-AutoOSLine '         has the token above can install software on this machine.' -Level warn
    }
    if ($forceDryRun) {
        Write-AutoOSLine '  Session is LOCKED to dry run - the browser cannot install anything.' -Level warn
    }
    Write-AutoOSLine '  The token changes every run. Ctrl-C to stop.' -Level muted
    Write-AutoOSLine ''

    try {
        Start-Process $url | Out-Null
    } catch {
        # Headless boxes have no default browser; the URL is printed above.
        Write-AutoOSLine 'Could not open a browser here - use the URL above.' -Level muted
    }

    while ($listener.IsListening) {
        $ctx = $listener.GetContext()
        $req = $ctx.Request
        $res = $ctx.Response
        $res.Headers.Add('Cache-Control', 'no-store')
        $res.Headers.Add('X-Content-Type-Options', 'nosniff')

        $send = {
            param($code, [byte[]]$bytes, $ctype)
            $res.StatusCode = $code
            $res.ContentType = $ctype
            $res.ContentLength64 = $bytes.Length
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
            $res.OutputStream.Close()
        }
        $json = {
            param($code, $obj)
            & $send $code ([Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Depth 8 -Compress))) 'application/json'
        }

        try {
            $path = $req.Url.AbsolutePath
            $given = $req.QueryString['token']
            $authed = ($given -eq $token)

            if ($path -eq '/' -or $path -eq '/index.html') {
                & $send 200 ([IO.File]::ReadAllBytes($indexPath)) 'text/html; charset=utf-8'
            }
            elseif (-not $authed) {
                & $json 403 @{ error = 'bad or missing token' }
            }
            elseif ($path -eq '/api/state') {
                & $json 200 (Get-AutoOSServeState -SystemInfo $SystemInfo -Catalog $Catalog)
            }
            elseif ($path -eq '/api/log') {
                $offset = 0
                [void][int]::TryParse($req.QueryString['offset'], [ref]$offset)
                $all = @($script:Log)
                $lines = if ($offset -lt $all.Count) { $all[$offset..($all.Count - 1)] } else { @() }
                & $json 200 @{
                    lines = @($lines); offset = $offset + @($lines).Count
                    running = $script:RunInfo.Running; done = $script:RunInfo.Done
                    total = $script:RunInfo.Total; summary = $script:RunInfo.Summary
                }
            }
            elseif ($path -eq '/api/install' -and $req.HttpMethod -eq 'POST') {
                if ($script:RunInfo.Running) {
                    & $json 409 @{ error = 'a run is already in progress' }
                } else {
                    $body = (New-Object IO.StreamReader($req.InputStream, $req.ContentEncoding)).ReadToEnd()
                    $payload = $body | ConvertFrom-Json
                    $ids = @($payload.ids)
                    if ($ids.Count -eq 0) {
                        & $json 400 @{ error = 'no components selected' }
                    } else {
                        $answers = @{}
                        if ($payload.PSObject.Properties.Name -contains 'answers' -and $payload.answers) {
                            foreach ($p in $payload.answers.PSObject.Properties) { $answers[$p.Name] = $p.Value }
                        }
                        $dry = $true
                        if ($payload.PSObject.Properties.Name -contains 'dryRun') { $dry = [bool]$payload.dryRun }
                        if ($forceDryRun) { $dry = $true }
                        Start-AutoOSInstallJob -RepoRoot $RepoRoot -Ids $ids -Answers $answers -DryRun $dry
                        & $json 202 @{ started = $true }
                    }
                }
            }
            else {
                & $json 404 @{ error = 'not found' }
            }
        } catch {
            $reason = $_.Exception.Message
            Write-AutoOSLine "request failed: $reason" -Level warn
            try {
                & $json 500 @{ error = $reason }
            } catch {
                # The client hung up mid-response; nothing left to report to.
                Write-AutoOSLine 'client disconnected before the error could be sent' -Level muted
            }
        }
    }
}

Export-ModuleMember -Function Start-AutoOSServer, Get-AutoOSServeState, Get-AutoOSLineLevel, Start-AutoOSInstallJob
