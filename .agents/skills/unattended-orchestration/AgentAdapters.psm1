<#
    AgentAdapters.psm1 — Heterogeneous CLI Agent Adapter Registry for Unattended Orchestration

    Defines command-line invocation templates, argument vectors, background execution
    modes, and installation/discovery heuristics for supported CLI agents.
#>

$script:AdapterRegistry = @{
    claude    = @{
        command            = "claude"
        start              = @("--bg", "--remote-control", "{{rcName}}", "{{tools}}", "--model", "{{model}}", "{{prompt}}")
        resume             = @("--bg", "--resume", "{{sessionId}}", "{{tools}}", "{{prompt}}")
        list               = @("agents", "--json")
        logs               = @("logs", "{{id}}")
        stop               = @("stop", "{{id}}")
        probe              = @("-p", "{{prompt}}", "--model", "{{probeModel}}", "--output-format", "json")
        allowedToolsFlag   = "--allowedTools"
        permissionModeFlag = "--permission-mode"
        idPattern          = "backgrounded[^\r\n]*?([0-9a-f]{8})"
        workingState       = "working"
        probeModel         = "sonnet"
        backgroundMode     = "native"
        leafEnforcement    = "--disallowedTools Agent"
        experimental       = $false
        # Why --model is not repeated on resume — UNVERIFIED, deliberately said
        # out loud. The agy adapter had the same shape and was measured
        # 2026-09-12 to run its global default model on every resumed turn; the
        # equivalent measurement has never been made for `claude --bg --resume`,
        # and neither `claude --help` nor this repo records whether a resumed
        # session restores the model it started with. Settle it by resuming a
        # session started on a non-default model and reading the model line in
        # its log; if it does not restore, add "--model", "{{model}}" here.
        resumeModelNote    = "UNVERIFIED: assumed to restore the session's own model; never measured"
    }
    agy       = @{
        command            = "agy"
        start              = @("-p", "{{prompt}}", "--model", "{{model}}", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "360m")
        # --model is REPEATED on resume. Measured 2026-09-12 (downstream project A, the
        # P8 lane): without it a resumed turn logs model="" and then "Propagating
        # selected model override to backend: label=Gemini 3.8 Flash (High)" -
        # agy's global default - while the runner's lane log still prints the
        # configured model, so the swap is invisible everywhere an operator looks.
        # `agy --help`: --model is "Model for the current CLI session", a global
        # flag, so it composes with --conversation.
        resume             = @("--conversation", "{{sessionId}}", "-p", "{{prompt}}", "--model", "{{model}}", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "360m")
        list               = $null
        logs               = $null
        stop               = $null
        probe              = @("-p", "{{prompt}}", "--model", "{{probeModel}}", "--output-format", "json", "--print-timeout", "30s")
        allowedToolsFlag   = $null
        permissionModeFlag = $null
        idPattern          = $null
        workingState       = $null
        probeModel         = "gemini-3.8-flash-high"
        backgroundMode     = "runner-managed"
        experimental       = $false
    }
    grok      = @{
        command            = "grok"
        start              = @("-p", "{{prompt}}", "--model", "{{model}}", "--always-approve", "--no-subagents", "--no-auto-update", "--sandbox", "workspace", "--output-format", "json")
        resume             = @("--resume", "{{sessionId}}", "-p", "{{prompt}}", "--always-approve", "--no-subagents", "--output-format", "json")
        list               = $null
        logs               = $null
        stop               = $null
        probe              = @("-p", "{{prompt}}", "--model", "{{probeModel}}", "--output-format", "json", "--no-auto-update")
        allowedToolsFlag   = $null
        permissionModeFlag = $null
        idPattern          = $null
        workingState       = $null
        probeModel         = "grok-code"
        backgroundMode     = "runner-managed"
        leafEnforcement    = "--no-subagents"
        experimental       = $true
        # UNVERIFIED, same as claude's: no run on this host has ever resumed a
        # grok session, so whether `--resume` keeps the started model is unknown.
        # Measure it before trusting a grok lane to more than one turn.
        resumeModelNote    = "UNVERIFIED: experimental adapter, never resumed on this host"
    }
    codewhale = @{
        command            = "codewhale"
        start              = @("exec", "--auto", "--model", "{{model}}", "--output-format", "json", "{{prompt}}")
        resume             = @("exec", "--resume", "{{sessionId}}", "--auto", "{{prompt}}")
        list               = $null
        logs               = $null
        stop               = $null
        probe              = @("exec", "--auto", "--model", "{{probeModel}}", "--output-format", "json", "{{prompt}}")
        allowedToolsFlag   = $null
        permissionModeFlag = $null
        idPattern          = $null
        workingState       = $null
        probeModel         = "deepseek-v4"
        backgroundMode     = "runner-managed"
        experimental       = $true
        # UNVERIFIED, same as grok's: never resumed on this host.
        resumeModelNote    = "UNVERIFIED: experimental adapter, never resumed on this host"
    }
}

$script:AdapterAliases = @{
    "antigravity" = "agy"
    "gemini"      = "agy"
    "deepseek"    = "codewhale"
    "claude-code" = "claude"
}

function Resolve-AdapterName([string]$Name) {
    if (-not $Name) { return "" }
    $n = $Name.Trim().ToLower()
    if ($script:AdapterAliases.ContainsKey($n)) {
        return $script:AdapterAliases[$n]
    }
    return $n
}

function Clone-HandoffObject($Obj) {
    if ($null -eq $Obj) { return $null }
    if ($Obj -is [hashtable]) {
        $c = @{}
        foreach ($k in $Obj.Keys) { $c[$k] = Clone-HandoffObject $Obj[$k] }
        return $c
    }
    if ($Obj -is [array]) {
        $arr = @()
        foreach ($i in $Obj) { $arr += , (Clone-HandoffObject $i) }
        return , $arr
    }
    return $Obj
}

function Get-AdapterTemplate([string]$Name, [switch]$IncludeExperimental) {
    $resolved = Resolve-AdapterName $Name
    if (-not $script:AdapterRegistry.ContainsKey($resolved)) {
        $valid = ($script:AdapterRegistry.Keys | Sort-Object) -join ", "
        throw "unknown agent adapter '$Name' (known: $valid)"
    }
    $tpl = $script:AdapterRegistry[$resolved]
    if ($tpl["experimental"] -and -not $IncludeExperimental) {
        throw "adapter '$resolved' is experimental; specify -IncludeExperimental to use it"
    }
    return (Clone-HandoffObject $tpl)
}

function Get-AdapterNames([switch]$IncludeExperimental) {
    $names = @()
    foreach ($k in ($script:AdapterRegistry.Keys | Sort-Object)) {
        if ($script:AdapterRegistry[$k]["experimental"] -and -not $IncludeExperimental) {
            continue
        }
        $names += $k
    }
    return $names
}

function Test-AdapterInstalled([string]$Name) {
    $resolved = Resolve-AdapterName $Name
    if (-not $script:AdapterRegistry.ContainsKey($resolved)) { return $false }
    $cmd = $script:AdapterRegistry[$resolved]["command"]

    $foundPath = $null
    $cmdObj = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($cmdObj) {
        $foundPath = $cmdObj.Source
    } else {
        # Check standard local install paths
        $candidates = @()
        if ($env:LOCALAPPDATA) {
            $candidates += (Join-Path $env:LOCALAPPDATA "$cmd\bin\$cmd.exe")
            $candidates += (Join-Path $env:LOCALAPPDATA "Programs\$cmd\$cmd.exe")
        }
        if ($env:USERPROFILE) {
            $candidates += (Join-Path $env:USERPROFILE ".$cmd\bin\$cmd.exe")
            $candidates += (Join-Path $env:USERPROFILE ".local\bin\$cmd.exe")
            $candidates += (Join-Path $env:USERPROFILE "AppData\Roaming\npm\$cmd.cmd")
        }
        foreach ($c in $candidates) {
            if ($c -and (Test-Path $c)) {
                $foundPath = $c
                break
            }
        }
    }

    if (-not $foundPath) { return $false }

    # For grok, verify vendor identity (xAI official vs community clone)
    if ($resolved -eq "grok") {
        try {
            $verOut = & $foundPath --version 2>&1 | Out-String
            if ($verOut -notmatch "xai") {
                return $false
            }
        } catch {
            return $false
        }
    }

    return $true
}

function Get-AdapterInstallCommand([string]$Name) {
    $resolved = Resolve-AdapterName $Name
    $isWindows = [System.Environment]::OSVersion.Platform -match "Win"
    switch ($resolved) {
        "claude" {
            return "npm install -g @anthropic-ai/claude-code"
        }
        "agy" {
            if ($isWindows) {
                return "irm https://antigravity.google/cli/install.ps1 | iex"
            }
            return "curl -fsSL https://antigravity.google/cli/install.sh | bash"
        }
        "grok" {
            return "npm install -g @xai-official/grok"
        }
        "codewhale" {
            return "npm install -g codewhale"
        }
        default {
            return $null
        }
    }
}

function Resolve-AdapterExecutable([string]$Command) {
    if (-not $Command) { return "" }
    $cmdObj = Get-Command $Command -ErrorAction SilentlyContinue
    if ($cmdObj) { return $cmdObj.Source }
    # Check standard local install paths
    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "$Command\bin\$Command.exe")
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\$Command\$Command.exe")
    }
    if ($env:USERPROFILE) {
        $candidates += (Join-Path $env:USERPROFILE ".$Command\bin\$Command.exe")
        $candidates += (Join-Path $env:USERPROFILE ".local\bin\$Command.exe")
        $candidates += (Join-Path $env:USERPROFILE "AppData\Roaming\npm\$Command.cmd")
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $Command
}

Export-ModuleMember -Function Get-AdapterTemplate, Get-AdapterNames, Test-AdapterInstalled, Get-AdapterInstallCommand, Resolve-AdapterName, Resolve-AdapterExecutable
