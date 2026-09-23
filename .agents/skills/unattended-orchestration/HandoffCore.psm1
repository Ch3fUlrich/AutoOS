<#
HandoffCore — the pure, testable half of the unattended session runner.

Everything here is a function of its arguments: no git, no `claude`, no clock
beyond the one the caller supplies. That split exists so the parts that decide
*what to do on failure* can be tested without an overnight run — the runner's
recovery logic used to be reachable only by actually hitting a usage limit at
03:00, which is not a place to discover a bad regex.

The impure half (worktrees, background sessions, merges) lives in
run_handoff_sessions.ps1. See SKILL.md for the model and the config contract.
#>

Set-StrictMode -Version Latest

if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "AgentAdapters.psm1"))) {
    Import-Module (Join-Path $PSScriptRoot "AgentAdapters.psm1") -ErrorAction SilentlyContinue
}

# --------------------------------------------------------------------------
# Defaults. Anything a repository is likely to share sits here; anything a
# repository must decide for itself is validated as required in Read-HandoffConfig.
# --------------------------------------------------------------------------
$script:HandoffDefaults = @{
    baseBranch     = "main"
    branchPrefix   = "handoff"
    stateDir       = "output/sessions"
    permissionMode = "auto"
    maxContinues   = 8
    maxRetries     = 40
    maxSessionHours = 6
    pollSeconds    = 30
    linkDirs       = @()
    copyDirs       = @()
    copyFiles      = @()
    maxDependencyHours = 48
    quietMinutes   = 20
    # The stall watchdog. quietMinutes closes a session that has already written
    # its DONE note; this one ends a child that is hung BEFORE writing one, which
    # the runner otherwise waits out at the launcher's own print-timeout (agy:
    # 360 min). 0 disables it. stallWindow is the rate rule: that many stalls
    # inside that many hours is a general problem, not a one-off, and stops the
    # lane for the operator instead of resuming into it again.
    stallMinutes   = 60
    stallWindow    = @{ maxStalls = 5; hours = 3 }
    stopAfterMerge = $true
    removeWorktreeAfterMerge = $false
    traps          = @()
    machineBudget  = $null
    controllerBrief = $null
    controllerBriefFile = $null
    allowedTools   = @("Bash", "Edit", "Write", "Read", "Glob", "Grep", "Agent", "ToolSearch", "Skill")
    guards         = $null
    postWorktree   = $null
    envFrom        = ".claude/settings.local.json"
    doneNote       = "{{planDir}}/session-{{session}}-DONE.md"

    # The session tool, as a swappable ADAPTER rather than a hardcoded binary.
    # Defaults describe Claude Code, so a copied skill works unedited; a repo
    # driving a different agent replaces only the keys it needs.
    #
    # {{tools}}  expands IN PLACE to: <allowedToolsFlag> <allowedTools...> <permissionModeFlag> <permissionMode>
    # {{prompt}} is substituted as exactly ONE argument, never re-expanded and never split.
    #
    # ORDER IS LOAD-BEARING in `start`: --allowedTools is variadic and swallows
    # everything up to the next flag, so a prompt placed after it becomes another
    # "tool name" and the session starts with NO task. {{tools}} therefore comes
    # first and {{prompt}} last, after a flag that takes exactly one value.
    launcher       = @{
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
    }
}

function Get-HandoffLinkType {
    <#  Junction on Windows, SymbolicLink everywhere else.

        `New-Item -ItemType Junction` simply does not exist off Windows, and the
        shared-artifact feature (linkDirs) is the only thing standing between a
        worktree and a regenerated multi-hundred-MB graph. Note $IsWindows is
        undefined on Windows PowerShell 5.1, where absence itself means Windows. #>
    $isWin = $true
    $v = Get-Variable -Name IsWindows -ErrorAction SilentlyContinue
    if ($v) { $isWin = [bool]$v.Value }
    if ($isWin) { return "Junction" } else { return "SymbolicLink" }
}

function Resolve-HandoffRepoRoot {
    <#  The git top-level containing $Path, or $null.

        This is what lets the skill be COPIED into an unknown repository and run
        there: the config need not name the checkout, so it carries no absolute
        path belonging to whoever generated it.  #>
    param([string]$Path = ".")
    $p = if (Test-Path $Path) { (Resolve-Path $Path).Path } else { $Path }
    $out = & git -C $p rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
        return ([string]$out).Trim().Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    }
    return $null
}

function Build-HandoffLaunchArgs {
    <#  Render one launcher action's argument vector.

        Kept pure and separate from the runner so the argument ORDER — the part
        that silently produces a session with no task when it is wrong — is
        covered by assertions rather than by an overnight run.  #>
    param([hashtable]$Config, [string]$Action, [hashtable]$Vars)
    $l = $Config["launcher"]
    if (-not $l -or -not $l.ContainsKey($Action) -or -not $l[$Action]) {
        $known = if ($l) { ($l.Keys | Where-Object { $l[$_] -is [array] } | Sort-Object) -join ", " } else { "none" }
        throw "launcher defines no '$Action' action (defined: $known)"
    }
    $argv = @()
    foreach ($a in @($l[$Action])) {
        if ($null -eq $a) { continue }
        $s = [string]$a
        if ($s -eq "{{tools}}") {
            $tools = @($Config["allowedTools"]) | Where-Object { $_ }
            if ($Vars.ContainsKey("isLeaf") -and $Vars["isLeaf"]) {
                # Tool-layer leaf enforcement: strip subagent tools from allowedTools
                $tools = @($tools | Where-Object { $_ -ne "Agent" -and $_ -ne "Workflow" })
            }
            if ($tools.Count -and $l.ContainsKey("allowedToolsFlag") -and $l["allowedToolsFlag"]) {
                $argv += $l["allowedToolsFlag"]; $argv += $tools
            }
            if ($Config["permissionMode"] -and $l.ContainsKey("permissionModeFlag") -and $l["permissionModeFlag"]) {
                $argv += $l["permissionModeFlag"]; $argv += [string]$Config["permissionMode"]
            }
            if ($Vars.ContainsKey("isLeaf") -and $Vars["isLeaf"] -and $l.ContainsKey("leafEnforcement") -and $l["leafEnforcement"]) {
                $argv += ($l["leafEnforcement"] -split '\s+')
            }
            continue
        }
        if ($s -eq "{{prompt}}") {
            # Appended raw: expanding it could re-substitute braces the brief
            # legitimately contains, and it must stay ONE argument.
            $argv += [string]$Vars["prompt"]
            continue
        }
        $argv += (Expand-HandoffTemplate $s $Vars)
    }
    return , $argv
}

function New-HandoffConfigScaffold {
    <#  Write a minimal, runnable config for the repository it is invoked in.

        Deliberately omits `repo`: the runner detects it, so the generated file
        contains no path belonging to the machine that generated it and can be
        committed and shared unchanged.  #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$RepoRoot,
        [string]$BaseBranch = "main",
        [switch]$Force
    )
    if ((Test-Path $Path) -and -not $Force) {
        throw "a handoff config already exists at $Path — pass -Force to overwrite it"
    }
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

    $promptFile = $null
    if ($RepoRoot) {
        $candidate = Join-Path $RepoRoot "prompts/unattended-orchestration.md"
        if (Test-Path $candidate) { $promptFile = $candidate }
    }
    if (-not $promptFile) {
        $root = Resolve-HandoffRepoRoot
        if ($root) {
            $candidate = Join-Path $root "prompts/unattended-orchestration.md"
            if (Test-Path $candidate) { $promptFile = $candidate }
        }
    }

    $controllerPrompt = if ($promptFile) {
        ((Get-Content $promptFile -Raw).Trim() + "`n`n## Active Sessions Table`n{{sessionsTable}}")
    } else {
        "Orchestrate the following sessions with proper subagent usage and worktree isolation:`n`n{{sessionsTable}}"
    }

    $scaffold = [ordered]@{
        '$comment'     = @(
            "Generated by run_handoff_sessions.ps1 -Init. Edit the sessions, then:",
            "  -Validate    check it     -EmitBriefs  read the briefs",
            "  -DryRun      rehearse it  (no flag)    run it",
            "",
            "'repo' is intentionally absent: the runner detects the git root, so this",
            "file carries no machine-specific path and can be committed as-is.",
            "NO SECRETS HERE - tokens are read from envFrom at preflight.",
            "launcher can be an adapter name ('claude', 'agy', 'grok', 'codewhale') or a custom adapter object."
        )
        launcher       = "claude"
        baseBranch     = $BaseBranch
        branchPrefix   = "handoff"
        stateDir       = "output/sessions"
        linkDirs       = @("graphify-out")
        copyFiles      = @(".env")
        subagents      = [ordered]@{
            maxConcurrent = 2
            maxDepth      = 3
            tiers         = [ordered]@{ mechanical = "sonnet"; judgement = "opus" }
            leafRule      = $true
        }
        controllerBrief = $controllerPrompt
        sessions       = [ordered]@{
            example = [ordered]@{
                name  = "example"
                model = "sonnet"
                brief = "Replace this with the actual task. State what done means, concretely enough that another agent could check it."
            }
        }
        briefTemplate  = "You are session {{session}} ({{sessionName}}). Your working directory is {{worktree}}, a git worktree on branch {{branch}}; the main checkout is {{repo}} and you never edit it.`n`n{{mcpPolicy}}`n`n{{brief}}`n`n{{subagentPolicy}}`n`nCommit small and often on {{branch}}, staging explicit paths only; never merge into {{baseBranch}} yourself. If a permission prompt blocks a command, record it and carry on with everything that does not depend on it. When done, write {{doneNote}} saying what landed, what remains and what blocked you, commit it, and stop."
        doneNote       = "{{stateDir}}/DONE-{{session}}.md"
        allowedTools   = @("Bash", "Edit", "Write", "Read", "Glob", "Grep", "Agent", "ToolSearch", "Skill")
        permissionMode = "auto"
    }
    ($scaffold | ConvertTo-Json -Depth 8) | Set-Content -Path $Path -Encoding utf8
    return $Path
}

function ConvertTo-HandoffHash {
    <#  PSCustomObject -> nested hashtable, arrays preserved.

        Windows PowerShell 5.1's ConvertFrom-Json has no -AsHashtable, and a
        state file the runner cannot read back loses every session id — and with
        it every resume. Converting explicitly keeps 5.1 and 7 on one path.  #>
    param($obj)
    if ($null -eq $obj) { return $null }
    if ($obj -is [hashtable]) { return $obj }
    if ($obj -is [string]) { return $obj }
    if ($obj -is [System.Collections.IEnumerable] -and $obj -isnot [string]) {
        # foreach + Add, never `| ForEach-Object`: the pipeline UNROLLS a nested
        # array, so [["E","A"],["D","B"]] came back as ["E","A","D","B"] and every
        # multi-session lane silently became one lane per session — turning
        # "run E then A in sequence" into "run E and A at once", which is the
        # exact store contention lanes exist to prevent.
        # The leading comma on the return is the other half: without it, a
        # single-lane array is unrolled away by the output stream.
        $list = New-Object System.Collections.ArrayList
        foreach ($item in $obj) { [void]$list.Add((ConvertTo-HandoffHash $item)) }
        return , $list.ToArray()
    }
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        $h = @{}
        foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = ConvertTo-HandoffHash $p.Value }
        return $h
    }
    return $obj
}

function Expand-HandoffTemplate {
    <#  Replace {{key}} with $vars[key], in ONE pass.

        Single-pass matters twice: a value that itself contains braces must not
        be re-expanded, and an unknown placeholder must survive verbatim rather
        than collapse to "". An emptied placeholder produces a brief that reads
        fine and silently omits the task.  #>
    param([string]$Template, [hashtable]$Vars)
    if (-not $Template) { return $Template }
    $evaluator = {
        param($m)
        $key = $m.Groups[1].Value
        if ($Vars -and $Vars.ContainsKey($key)) { return [string]$Vars[$key] }
        return $m.Value
    }
    return [regex]::Replace($Template, '\{\{(\w+)\}\}', $evaluator)
}

function Classify-HandoffFailure {
    <#  -> @{ kind = "auth"|"limit"|"transient"|"other"; wait = seconds }

        Order is load-bearing. Auth is tested first because it is the one kind
        that cannot be waited out: an expired OAuth session fails every launch
        instantly, and retrying it burns the whole night. Everything else is a
        wait, and the epoch form of the usage limit is preferred over the flat
        30 minutes because Claude tells you exactly when it resets.  #>
    param([string]$Text)
    $t = if ($Text) { $Text.ToLower() } else { "" }
    # One owner for the ceiling: every branch that reads a reset time out of a
    # provider's own message clamps to the same six hours, so the number is
    # written once rather than once per provider wording.
    $capSeconds = 6 * 3600

    if ($t -match "failed to authenticate|oauth|not logged in|please run /login|claude login|invalid api key|authentication") {
        return @{ kind = "auth"; wait = 0 }
    }
    if ($t -match "usage limit reached\|(\d{9,11})") {
        $reset = [DateTimeOffset]::FromUnixTimeSeconds([long]$Matches[1]).LocalDateTime
        $wait = [int]([math]::Max(60, ($reset - (Get-Date)).TotalSeconds + 90))
        return @{ kind = "limit"; wait = [int][math]::Min($wait, $capSeconds) }
    }
    # Antigravity says none of the words below. Measured 2026-09-12 08:41 on a
    # live lane, it reports exhaustion as "Individual quota reached ... Resets in
    # 98h52m33s" (and, on a retry, "RESOURCE_EXHAUSTED (code 429)"). The JSON form
    # carries no "limit reached" and no "429", so a 99-hour wall was classified
    # `other` and resumed as an early stop eight times. Sits below the epoch
    # branch because an epoch is exact where "Resets in" is rounded prose.
    if ($t -match "quota reached|quota exceeded|resource_exhausted") {
        $m = [regex]::Match($t, "resets in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
        if ($m.Success) {
            $secs = 0
            if ($m.Groups[1].Success) { $secs += [int]$m.Groups[1].Value * 3600 }
            if ($m.Groups[2].Success) { $secs += [int]$m.Groups[2].Value * 60 }
            if ($m.Groups[3].Success) { $secs += [int]$m.Groups[3].Value }
            # Every group is optional, so "resets in" with no number still matches
            # with a zero total -- that is no reset time at all, not a zero wait.
            if ($secs -gt 0) { return @{ kind = "limit"; wait = [int][math]::Min($secs, $capSeconds) } }
        }
        return @{ kind = "limit"; wait = 1800 }
    }
    # The session-limit banner ("You've hit your session limit · resets 4:10pm
    # (Europe/Berlin)") carries a wall-clock reset. Measured 2026-09-17: the
    # generic branch below waited a flat 30 min, resumed while the limit still
    # held, counted six instant stops as early stops and merged two lanes
    # unfinished. Sleep until the stated time (+90 s), never past six hours.
    # (Written in 3df910e; re-applied here after that commit's copy of this file
    # turned out to predate three functions the driver calls.)
    if ($t -match "session limit[^`n]{0,40}resets\s+(\d{1,2}):(\d{2})\s*(am|pm)") {
        $h = [int]$Matches[1]; $m = [int]$Matches[2]; $ap = $Matches[3]
        if ($ap -eq "pm" -and $h -lt 12) { $h += 12 }
        if ($ap -eq "am" -and $h -eq 12) { $h = 0 }
        $reset = (Get-Date).Date.AddHours($h).AddMinutes($m)
        if ($reset -le (Get-Date)) { $reset = $reset.AddDays(1) }
        $wait = [int]([math]::Max(60, ($reset - (Get-Date)).TotalSeconds + 90))
        return @{ kind = "limit"; wait = [int][math]::Min($wait, 6 * 3600) }
    }
    if ($t -match "usage limit|session limit|rate limit|limit reached|too many requests|429") {
        return @{ kind = "limit"; wait = 1800 }
    }
    if ($t -match "overloaded|529|500 internal|internal server error|econnreset|etimedout|enotfound|socket hang up|fetch failed|network|timed out|503|502") {
        return @{ kind = "transient"; wait = 300 }
    }
    return @{ kind = "other"; wait = 120 }
}

function Test-HandoffPermissionPrompt {
    <#  A background session cannot answer a prompt; it sits `blocked` forever.
        Detecting it turns a silent all-night stall into a state the operator
        can see in the state file the next morning.  #>
    param([string]$Text)
    if (-not $Text) { return $false }
    # The MCP-approval and folder-trust dialogs are drawn as full-screen
    # menus, not questions: match their wording, not only a question mark.
    return [bool]($Text -match "(?i)do you want to proceed|do you trust|allow this|\[y/n\]|permission required|new mcp server found|use this mcp server|use this and all future mcp servers|enter to confirm|esc to cancel|trust the files in this folder")
}

function Read-HandoffState {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    try {
        $h = ConvertTo-HandoffHash (Get-Content $Path -Raw | ConvertFrom-Json)
        if ($h -is [hashtable]) { return $h }
        return @{}
    }
    catch { return @{} }   # a corrupt state file must not stop the run; it restarts from scratch
}

function Write-HandoffState {
    param([string]$Path, [hashtable]$State)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $State | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding utf8
}

function Redact-HandoffSecrets {
    <#  Recursively redact secrets, credentials, tokens, and keys from any object
        before persisting to the unattended ledger or log files. #>
    param([object]$Data)

    if ($null -eq $Data) { return $null }

    if ($Data -is [System.Collections.IDictionary]) {
        $clean = @{}
        foreach ($k in $Data.Keys) {
            $keyStr = [string]$k
            if ($keyStr -match '(?i)(^token$|^secret$|^password$|^credential$|^bearer$|api[_-]?key|secret[_-]?key|access[_-]?key|private[_-]?key|auth)') {
                $clean[$k] = "[REDACTED]"
            } else {
                $clean[$k] = Redact-HandoffSecrets $Data[$k]
            }
        }
        return $clean
    }

    if ($Data -is [System.Management.Automation.PSCustomObject]) {
        $clean = [ordered]@{}
        foreach ($p in $Data.PSObject.Properties) {
            if ($p.Name -match '(?i)(^token$|^secret$|^password$|^credential$|^bearer$|api[_-]?key|secret[_-]?key|access[_-]?key|private[_-]?key|auth)') {
                $clean[$p.Name] = "[REDACTED]"
            } else {
                $clean[$p.Name] = Redact-HandoffSecrets $p.Value
            }
        }
        return [pscustomobject]$clean
    }

    if ($Data -is [System.Collections.IEnumerable] -and $Data -isnot [string]) {
        $cleanList = @()
        foreach ($item in $Data) {
            $cleanList += , (Redact-HandoffSecrets $item)
        }
        return , $cleanList
    }

    if ($Data -is [string]) {
        $s = $Data
        $s = [regex]::Replace($s, "(?i)bearer\s+[a-zA-Z0-9_\-\.]{8,}", "Bearer [REDACTED]")
        $s = [regex]::Replace($s, "(sk-[a-zA-Z0-9_\-]{20,}|gh[pousr]_[a-zA-Z0-9]{20,})", "[REDACTED]")
        try {
            $envVars = [System.Environment]::GetEnvironmentVariables()
            foreach ($envKey in $envVars.Keys) {
                if ($envKey -match '(?i)(token|secret|key|password|credential|auth)') {
                    $val = [string]$envVars[$envKey]
                    if ($val -and $val.Trim().Length -ge 8 -and $s.Contains($val)) {
                        $s = $s.Replace($val, "[REDACTED]")
                    }
                }
            }
        } catch { }
        return $s
    }

    return $Data
}

function Rotate-HandoffLedger {
    <#  Rotate a ledger file if it exceeds MaxSizeMB.
        Rotates path -> path.1 -> path.2 ... up to MaxRotations. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [double]$MaxSizeMB = 10.0,
        [int]$MaxRotations = 5
    )
    if (-not (Test-Path $Path -PathType Leaf)) { return $false }
    $item = Get-Item $Path -ErrorAction SilentlyContinue
    $thresholdBytes = [long]($MaxSizeMB * 1024 * 1024)
    if (-not $item -or $item.Length -lt $thresholdBytes) { return $false }

    for ($i = $MaxRotations - 1; $i -ge 1; $i--) {
        $src = "$Path.$i"
        $dst = "$Path.$($i + 1)"
        if (Test-Path $src) {
            if (Test-Path $dst) { Remove-Item -Force $dst -ErrorAction SilentlyContinue }
            Move-Item -Force -Path $src -Destination $dst -ErrorAction SilentlyContinue
        }
    }
    $firstRot = "$Path.1"
    if (Test-Path $firstRot) { Remove-Item -Force $firstRot -ErrorAction SilentlyContinue }
    Move-Item -Force -Path $Path -Destination $firstRot -ErrorAction SilentlyContinue
    return $true
}

function Write-HandoffLedgerEvent {
    <#  Append a single-line, schema-validated, secret-redacted NDJSON event
        to the unattended handoff progress ledger. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$Event,
        [double]$MaxSizeMB = 10.0,
        [int]$MaxRotations = 5
    )
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

    Rotate-HandoffLedger -Path $Path -MaxSizeMB $MaxSizeMB -MaxRotations $MaxRotations | Out-Null

    $nowIso = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $evtId = if ($Event.ContainsKey("eventId") -and $Event["eventId"]) { [string]$Event["eventId"] } else { [guid]::NewGuid().ToString("D") }
    $ver = if ($Event.ContainsKey("schemaVersion") -and $Event["schemaVersion"]) { [int]$Event["schemaVersion"] } else { 1 }
    $ts = if ($Event.ContainsKey("timestamp") -and $Event["timestamp"]) { [string]$Event["timestamp"] } else { $nowIso }
    $sessId = if ($Event.ContainsKey("sessionId") -and $Event["sessionId"]) { [string]$Event["sessionId"] } else { "" }
    $turn = if ($Event.ContainsKey("turnId")) { $Event["turnId"] } else { $null }
    $parent = if ($Event.ContainsKey("parentEventId")) { $Event["parentEventId"] } else { $null }
    $kind = if ($Event.ContainsKey("kind") -and $Event["kind"]) { [string]$Event["kind"] } else { "unknown" }
    $corr = if ($Event.ContainsKey("correlationId")) { $Event["correlationId"] } else { $null }
    $data = if ($Event.ContainsKey("data") -and $null -ne $Event["data"]) { $Event["data"] } else { @{} }

    $payload = [ordered]@{
        schemaVersion = $ver
        eventId       = $evtId
        timestamp     = $ts
        sessionId     = $sessId
        turnId        = $turn
        parentEventId = $parent
        kind          = $kind
        correlationId = $corr
        data          = $data
    }

    $redacted = Redact-HandoffSecrets $payload
    $json = ($redacted | ConvertTo-Json -Depth 10 -Compress) -replace "[\r\n]+", " "

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $lineBytes = $utf8NoBom.GetBytes("$json`n")
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    try {
        $fs.Write($lineBytes, 0, $lineBytes.Length)
    }
    finally {
        $fs.Dispose()
    }

    return $redacted
}

function Read-HandoffLedger {
    <#  Read and parse events from an NDJSON ledger file, tolerating corrupted or unclosed lines.
        Optionally filter by SessionId and/or Kind. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$SessionId = "",
        [string]$Kind = ""
    )
    if (-not (Test-Path $Path -PathType Leaf)) { return , @() }

    $events = @()
    $fs = $null
    $reader = $null
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
        while (-not $reader.EndOfStream) {
            $line = $reader.ReadLine()
            if ($null -eq $line) { break }
            $trimmed = $line.Trim()
            if (-not $trimmed) { continue }
            try {
                $raw = $trimmed | ConvertFrom-Json
                $evt = ConvertTo-HandoffHash $raw
                if ($SessionId -and (-not $evt.ContainsKey("sessionId") -or $evt["sessionId"] -ne $SessionId)) { continue }
                if ($Kind -and (-not $evt.ContainsKey("kind") -or $evt["kind"] -ne $Kind)) { continue }
                $events += $evt
            }
            catch {
                continue
            }
        }
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($fs) { $fs.Dispose() }
    }
    return , $events
}

function Reconcile-HandoffLedger {
    <#  Audit state.json against live processes and the ledger.
        If a session was recorded as 'running' or 'working' but its process died
        (or runner restarted after host crash), mark it 'crashed' and append a
        'crashed' event to the ledger so the next pass resumes cleanly. #>
    param(
        [string]$LedgerPath = "",
        [Parameter(Mandatory)][string]$StatePath,
        # Live background-session ids from the launcher (`claude agents --json`).
        # $null means "no liveness source": a session without a pid is then
        # NEVER judged dead. Measured 2026-09-10: without this every lane child
        # marked every other lane's running session crashed at its own startup,
        # because the claude launcher records no pid, and every dependent lane
        # stopped on the false status.
        [string[]]$AliveBgIds = $null
    )
    if (-not (Test-Path $StatePath -PathType Leaf)) { return , @() }
    $state = Read-HandoffState $StatePath
    if (-not $state -or $state.Keys.Count -eq 0) { return , @() }

    $reconciled = @()
    $dirty = $false

    foreach ($key in @($state.Keys)) {
        $entry = $state[$key]
        if ($entry -isnot [hashtable]) { continue }
        $st = if ($entry.ContainsKey("status")) { [string]$entry["status"] } else { "" }
        if ($st -in @("running", "working")) {
            $alive = $false
            if ($entry.ContainsKey("pid") -and $entry["pid"]) {
                try {
                    $pidNum = [int]$entry["pid"]
                    $p = Get-Process -Id $pidNum -ErrorAction SilentlyContinue
                    if ($p -and -not $p.HasExited) { $alive = $true }
                } catch { $alive = $false }
            }
            elseif ($entry.ContainsKey("bg_id") -and $entry["bg_id"]) {
                if ($null -eq $AliveBgIds) { $alive = $true }
                elseif (@($AliveBgIds) -contains [string]$entry["bg_id"]) { $alive = $true }
            }
            else { $alive = $true }   # nothing to judge by: never guess "dead"
            if (-not $alive) {
                $oldStatus = $st
                $entry["status"] = "crashed"
                $entry["last_error"] = "session crashed (reconciled by runner startup)"
                $entry["updated"] = (Get-Date -Format "s")
                $dirty = $true
                $reconciled += $key

                if ($LedgerPath) {
                    try {
                        Write-HandoffLedgerEvent -Path $LedgerPath -Event @{
                            sessionId = $key
                            kind      = "crashed"
                            data      = @{
                                reason         = "session process died ungracefully without terminal status"
                                previousStatus = $oldStatus
                                pid            = if ($entry.ContainsKey("pid")) { $entry["pid"] } else { $null }
                                bgId           = if ($entry.ContainsKey("bg_id")) { $entry["bg_id"] } else { $null }
                            }
                        } | Out-Null
                    } catch { }
                }
            }
        }
    }

    if ($dirty) {
        Write-HandoffState $StatePath $state
    }
    return , $reconciled
}

function Build-HandoffArchiveManifest {
    <#  Construct the list of files and artifacts to archive for a completed session.
        Pure: returns a manifest hashtable without making filesystem changes. #>
    param(
        [hashtable]$Config,
        [string]$SessionKey,
        [hashtable]$Vars,
        [string]$Timestamp = ""
    )
    $ts = if ($Timestamp) { $Timestamp } else { (Get-Date -Format "yyyyMMdd-HHmmss") }
    $stateDir = if ([System.IO.Path]::IsPathRooted($Config["stateDir"])) { $Config["stateDir"] }
                else { Join-Path $Config["repo"] $Config["stateDir"] }
    $archiveRoot = Join-Path $stateDir "archive"
    $destDir = Join-Path $archiveRoot "$SessionKey-$ts"

    $wt = if ($Vars.ContainsKey("worktree")) { [string]$Vars["worktree"] } else { "" }
    $branch = if ($Vars.ContainsKey("branch")) { [string]$Vars["branch"] } else { "" }
    $doneNote = if ($Vars.ContainsKey("doneNote")) { [string]$Vars["doneNote"] } else { "" }

    $filesToCopy = @()
    if ($doneNote) {
        $fullDone = if ([System.IO.Path]::IsPathRooted($doneNote)) { $doneNote } else { Join-Path $Config["repo"] $doneNote }
        $filesToCopy += @{ source = $fullDone; target = "DONE.md" }
    }

    $logFile = Join-Path $stateDir "session-$SessionKey.log"
    $filesToCopy += @{ source = $logFile; target = "session.log" }

    return @{
        sessionKey  = $SessionKey
        timestamp   = $ts
        archiveDir  = $destDir
        worktree    = $wt
        branch      = $branch
        files       = $filesToCopy
    }
}

function Archive-HandoffSession {
    <#  Execute session archival: copies artifacts to the archive directory,
        writes manifest.json, updates state, and appends an archived ledger event.
        Idempotent: skips if already archived. #>
    param(
        [hashtable]$Config,
        [string]$SessionKey,
        [hashtable]$Vars,
        [string]$LedgerPath = "",
        [string]$StatePath = "",
        [string]$CorrelationId = ""
    )
    $manifest = Build-HandoffArchiveManifest $Config $SessionKey $Vars
    $dest = $manifest.archiveDir
    if (-not (Test-Path $dest)) {
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
    }

    $copied = @()
    foreach ($item in $manifest.files) {
        $src = [string]$item.source
        $tgtName = [string]$item.target
        if ($src -and (Test-Path $src)) {
            $tgtPath = Join-Path $dest $tgtName
            Copy-Item -Force $src $tgtPath -ErrorAction SilentlyContinue
            $copied += $tgtName
        }
    }

    # Extract git patch if repo and branch are available
    $repo = [string]$Config["repo"]
    $branch = [string]$manifest.branch
    $baseBranch = if ($Config.ContainsKey("baseBranch")) { [string]$Config["baseBranch"] } else { "main" }
    if ($repo -and (Test-Path $repo) -and $branch) {
        try {
            $patch = & git -C $repo diff "$baseBranch...$branch" 2>$null
            if ($patch) {
                $patchPath = Join-Path $dest "changes.patch"
                $patch | Set-Content -Path $patchPath -Encoding utf8
                $copied += "changes.patch"
            }
        } catch { }
    }

    # Write manifest.json
    $meta = @{
        sessionKey   = $SessionKey
        archivedAt   = (Get-Date -Format "o")
        branch       = $branch
        baseBranch   = $baseBranch
        copiedFiles  = $copied
    }
    $metaPath = Join-Path $dest "manifest.json"
    $meta | ConvertTo-Json -Depth 4 | Set-Content -Path $metaPath -Encoding utf8

    # Update ledger if path provided
    if ($LedgerPath) {
        Write-HandoffLedgerEvent -Path $LedgerPath -Event @{
            sessionId     = $SessionKey
            turnId        = 0
            kind          = "archived"
            correlationId = $CorrelationId
            data          = @{
                archiveDir = $dest
                files      = $copied
                branch     = $branch
            }
        } | Out-Null
    }

    # Update state if path provided
    if ($StatePath -and (Test-Path $StatePath)) {
        try {
            $st = Read-HandoffState $StatePath
            if ($st.ContainsKey($SessionKey) -and $st[$SessionKey] -is [hashtable]) {
                $st[$SessionKey]["archived"] = (Get-Date -Format "s")
                $st[$SessionKey]["archive_path"] = $dest
                Write-HandoffState $StatePath $st
            }
        } catch { }
    }

    return $dest
}

function Resolve-HandoffBrief {
    <#  A session's brief is either inline (`brief`) or a file (`briefFile`,
        relative to the config's briefRoot, else the repo). Inline wins.  #>
    param([hashtable]$Config, [hashtable]$Session)
    if ($Session.ContainsKey("brief") -and $Session["brief"]) { return [string]$Session["brief"] }
    if ($Session.ContainsKey("briefFile") -and $Session["briefFile"]) {
        $root = if ($Config.ContainsKey("briefRoot") -and $Config["briefRoot"]) { $Config["briefRoot"] } else { $Config["repo"] }
        $p = if ([System.IO.Path]::IsPathRooted($Session["briefFile"])) { $Session["briefFile"] } else { Join-Path $root $Session["briefFile"] }
        if (-not (Test-Path $p)) { throw "briefFile not found: $p" }
        return (Get-Content $p -Raw)
    }
    throw "session has neither 'brief' nor 'briefFile'"
}

function Build-HandoffGuardCommand {
    <#  -> @{ command; args } or $null when the repo declares no guards.

        Guards are deliberately OPT-IN and fully generic: pytest, npm test,
        cargo, a shell script. The previous runner hardcoded a pytest invocation
        and a venv path, which is exactly the part no other repository can use.
        `{{guards}}` in args expands in place to the `paths` list.  #>
    param([hashtable]$Config, [hashtable]$Vars)
    if (-not $Config.ContainsKey("guards") -or -not $Config["guards"]) { return $null }
    $g = $Config["guards"]
    if (-not $g.ContainsKey("command") -or -not $g["command"]) { throw "guards.command is required when guards are declared" }
    $paths = if ($g.ContainsKey("paths") -and $g["paths"]) { @($g["paths"]) } else { @() }
    $argv = @()
    foreach ($a in @($g["args"])) {
        if ($null -eq $a) { continue }
        if ([string]$a -eq "{{guards}}") { $argv += $paths; continue }
        $argv += (Expand-HandoffTemplate ([string]$a) $Vars)
    }
    return @{ command = (Expand-HandoffTemplate ([string]$g["command"]) $Vars); args = $argv }
}

function Build-HandoffSubagentPolicy {
    <#  The paragraph that tells a session HOW to use subagents, rendered from
        config so the policy is stated once and reaches every brief identically.

        Opt-in: with no `subagents` block this returns "" and briefs say nothing
        about subagents, because a session told to delegate with no cap and no
        tier assignment delegates badly. The leaf rule (subagents do not spawn
        subagents) mirrors `swarm-orchestration`; without it a wave of agents
        fans out geometrically and the budget is gone before the work is.

        $SessionKey is optional and names the session the policy is being built
        for: the leaf-enforcement flag is a CLI flag of the agent that session
        actually drives, so a session on another adapter must not be handed the
        default adapter's flag. Omitted, it behaves exactly as before.  #>
    param([hashtable]$Config, [string]$SessionKey = "")
    if (-not $Config.ContainsKey("subagents") -or -not $Config["subagents"]) { return "" }
    $s = $Config["subagents"]
    $cap = if ($s.ContainsKey("maxConcurrent") -and $s["maxConcurrent"]) { [int]$s["maxConcurrent"] } else { 2 }
    $tiers = if ($s.ContainsKey("tiers") -and $s["tiers"]) { $s["tiers"] } else { @{} }
    $mech = if ($tiers.ContainsKey("mechanical")) { $tiers["mechanical"] } else { "sonnet" }
    $judge = if ($tiers.ContainsKey("judgement")) { $tiers["judgement"] } else { "opus" }
    $parts = @(
        "Orchestrate this work with subagents rather than doing it all in your own context:",
        "spawn $mech subagents for mechanical, closed-file work (mechanical edits, test scaffolding,",
        "renames, doc sweeps) and $judge subagents for judgement (design calls, ambiguous",
        "requirements, review), at most $cap at a time."
    )
    if (-not $s.ContainsKey("leafRule") -or $s["leafRule"]) {
        $maxDepth = if ($s.ContainsKey("maxDepth") -and $s["maxDepth"]) { [int]$s["maxDepth"] } else { 3 }
        $parts += "Delegation depth is capped at $maxDepth levels (Main orchestrator -> phase-specific orchestrator subagent -> task-specific subagent orchestrator)."
        $parts += "Subagents at level $maxDepth are leaves: they do NOT spawn further subagents."
        $launcher = if ($SessionKey -and $Config.ContainsKey("sessions") -and $Config["sessions"] -and $Config["sessions"].ContainsKey($SessionKey)) {
            Resolve-HandoffSessionLauncher $Config $SessionKey $Config["launcher"]
        } else { $Config["launcher"] }
        $adapterName = if ($launcher -and $launcher.ContainsKey("command")) { [string]$launcher["command"] } else { "claude" }
        $leafFlag = switch ($adapterName) {
            "grok"   { "--no-subagents" }
            "claude" { "--disallowedTools Agent" }
            default  { $null }
        }
        if ($leafFlag) {
            $parts += "When spawning Level $maxDepth leaf subagents, enforce this at the tool layer with the CLI flag: $leafFlag."
        }
    }
    $parts += "Give each one a self-contained task and an explicit return contract; never delegate a vague task."
    return ($parts -join " ")
}

function Get-HandoffDependencyState {
    <#  -> @{ ready; waiting = @(keys); failed = @(keys) } for one session's dependsOn.

        Pure: reads only the state hashtable it is given, so the wait/stop
        decision is testable without a run. "merged" satisfies a dependency;
        "done-unmerged" (a -NoMerge run) is accepted too, because the operator
        chose not to merge and a dependent that waits forever helps nobody. A
        terminal non-merged status FAILS the dependent rather than leaving it
        polling all night for a merge that cannot come.  #>
    param([hashtable]$State, [string[]]$Deps)
    $satisfied = @("merged", "done-unmerged", "guards-green")
    # A red guard and a merge conflict are the two states an operator resolves by
    # hand within minutes (measured 2026-09-05: three of nine merges), after which
    # the runner marks the session merged-elsewhere. Failing every dependent the
    # moment they appear cost the final-suite lane three re-queues in one day, so
    # they are RECOVERABLE: the dependent keeps waiting, bounded by
    # maxDependencyHours. Terminal is what no operator action turns into a merge.
    $recoverable = @("guards-red", "merge-conflict")
    $terminal = @("failed", "auth-failed", "blocked", "crashed",
                  "dependency-failed", "dependency-timeout", "stopped-by-operator")
    $waiting = @(); $failed = @()
    foreach ($d in @($Deps)) {
        if (-not $d) { continue }
        $status = ""
        if ($State -and $State.ContainsKey($d) -and $State[$d] -is [hashtable] -and $State[$d].ContainsKey("status")) {
            $status = [string]$State[$d]["status"]
        }
        if ($satisfied -contains $status) { continue }
        if ($terminal -contains $status) { $failed += $d; continue }
        $waiting += $d   # running, waiting, unknown - or recoverable (guards-red, merge-conflict)
    }
    return @{ ready = (($waiting.Count -eq 0) -and ($failed.Count -eq 0)); waiting = $waiting; failed = $failed }
}

function Build-HandoffTraps {
    <#  The `traps` list rendered as one block for every brief, so a trap one
        session measured is not re-measured by the next. Empty when unset. #>
    param([hashtable]$Config)
    if (-not $Config.ContainsKey("traps") -or -not $Config["traps"]) { return "" }
    $items = @($Config["traps"]) | Where-Object { $_ }
    if (-not $items.Count) { return "" }
    $lines = @("Measured traps - do not re-measure them, work around them:")
    foreach ($t in $items) { $lines += "- $t" }
    return ($lines -join "`n")
}

function Test-HandoffDoneQuiet {
    <#  Has a session FINISHED even though its turn has not ended?

        Measured 2026-09-04 (downstream project A, lanes C and F7): a session wrote its
        DONE note, made its last commit, and then reported `working` for three
        hours; the runner waited on the turn, hit maxSessionHours, stopped the
        finished session and resumed it - twice - each resume a fresh session
        that read the brief, found the work done, and idled. The evidence of
        completion is the DONE note plus a quiet, clean branch, not the turn.  #>
    param([bool]$DoneExists, [long]$LastCommitEpoch, [long]$NowEpoch, [int]$QuietMinutes, [bool]$Dirty)
    if (-not $DoneExists) { return $false }
    if ($Dirty) { return $false }
    if ($LastCommitEpoch -le 0) { return $false }
    return (($NowEpoch - $LastCommitEpoch) -ge ($QuietMinutes * 60))
}

function Test-HandoffStalled {
    <#  Is this child HUNG, as opposed to finished or quietly working?

        Test-HandoffDoneQuiet only ever closes a session that has already written
        its DONE note. Measured 2026-09-12 (downstream project A, an agy lane): a child
        in print mode sat for 48 min with an empty stdout, no commit and no DONE
        note, and nothing in the runner noticed - the lane was waiting for the
        launcher's own --print-timeout of 360 min.

        The reason this takes THREE signals rather than the obvious two is the
        same day's other measurement: an agy child that looked identical from
        outside - empty stdout, no commit, no dirty file, 35 min of it - was
        blocked on a terminal command it had started itself (a pytest run) and
        was working the whole time. It flushed a 92 KB transcript, 7 commits and
        a DONE note and exited normally. A two-signal detector would have ended
        it 35 min into finishing its plan; the CPU of that child PROCESS is what
        separates the two cases.
        So a child is stalled only when its log, its worktree AND its process
        tree have ALL been quiet for StallMinutes:

          LastLogEpoch     newest write to the session log the launcher actually
                           streams (a print-mode launcher's stdout is written
                           once, at turn end, so the driver passes only its .err)
          LastChangeEpoch  newest commit or working-tree change in the worktree
          LastCpuEpoch     the last time the child's process tree accrued CPU

        An epoch of 0 means the runner could not read that signal (no log file
        yet; no pid to sum CPU over, which is every launcher that is not
        runner-managed). Unknown is NOT silence: it returns $false, so a missing
        signal can only ever fail to fire the watchdog, never fire it wrongly.  #>
    param([long]$LastLogEpoch, [long]$LastChangeEpoch, [long]$LastCpuEpoch,
          [bool]$DoneExists, [long]$NowEpoch, [int]$StallMinutes)
    if ($StallMinutes -le 0) { return $false }   # the watchdog is switched off
    if ($DoneExists) { return $false }           # finished, not hung: quietMinutes owns this
    $cut = [long]$StallMinutes * 60
    foreach ($e in @($LastLogEpoch, $LastChangeEpoch, $LastCpuEpoch)) {
        if ([long]$e -le 0) { return $false }                    # unreadable signal
        if (($NowEpoch - [long]$e) -lt $cut) { return $false }   # something moved
    }
    return $true
}

function Test-HandoffStallBudgetExceeded {
    <#  Has this session stalled often enough, close enough together, to be a
        GENERAL problem rather than a one-off?

        The operator's rule: five stalls inside about three hours means the lane
        is not going to recover by being resumed a sixth time - stop and leave it
        for a human. Five spread across a night are five one-offs, each of which
        the resume path handled, so stalls older than the window drop out of the
        count instead of accumulating forever.  #>
    param([array]$StallEpochs, [long]$NowEpoch, [int]$MaxStalls, [double]$Hours)
    if ($MaxStalls -le 0 -or $Hours -le 0) { return $false }
    $cut = $NowEpoch - [long]($Hours * 3600)
    $inWindow = @(@($StallEpochs) | Where-Object { $_ -and ([long]$_ -ge $cut) })
    return ($inWindow.Count -ge $MaxStalls)
}

function Test-HandoffResourceConflict {
    <#  -> the keys of running sessions whose held resources conflict with $Wanted.

        A resource is "name" or "name:read" / "name:write" (bare = write).
        Write excludes everything on that name; read excludes only write.
        Measured need (downstream project A, 2026-09-04): a read-only evidence job on
        a single-holder HDF5 store starved the writing pass; the orchestrator
        coordinated by hand. Lanes order sessions; this excludes them.  #>
    param([array]$Held, [string[]]$Wanted)
    $want = @{}
    foreach ($w in @($Wanted)) {
        if (-not $w) { continue }
        $parts = ([string]$w).Split(":", 2)
        $mode = if ($parts.Count -gt 1) { $parts[1].Trim().ToLower() } else { "write" }
        $want[$parts[0].Trim()] = $mode
    }
    $conflicts = @()
    foreach ($h in @($Held)) {
        if (-not $h -or -not ($h -is [hashtable])) { continue }
        foreach ($r in @($h["resources"])) {
            if (-not $r) { continue }
            $parts = ([string]$r).Split(":", 2)
            $name = $parts[0].Trim()
            $mode = if ($parts.Count -gt 1) { $parts[1].Trim().ToLower() } else { "write" }
            if ($want.ContainsKey($name) -and ($mode -eq "write" -or $want[$name] -eq "write")) {
                if ($conflicts -notcontains [string]$h["key"]) { $conflicts += [string]$h["key"] }
            }
        }
    }
    # Plain return: callers wrap the result in @(...). A leading-comma return
    # arrives at an @() caller as ONE element holding the whole array.
    return $conflicts
}

function Get-HandoffRefusalCount {
    <#  How many lines of a DONE note report a refusal by the permission
        classifier. Crude on purpose: it counts sentences that say something
        WAS refused and skips the ones that say nothing was, so the morning
        reader sees the pattern across lanes without opening every note.  #>
    param([string]$Text)
    if (-not $Text) { return 0 }
    # Only the note's own "## Refused" section is read when it has one. A
    # repository whose house style is "refuse rather than estimate" writes the
    # word on every other line of Landed and Evidence (one note: 47 lines, none
    # a classifier refusal), and counting those made a clean session look like
    # the worst one in the run.
    $lines = @($Text -split "`r?`n")
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*#{1,6}\s*Refused\b") { $start = $i; break } }
    if ($start -ge 0) {
        $end = $lines.Count
        for ($i = $start + 1; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*#{1,2}\s+\S") { $end = $i; break } }
        $lines = @($lines[$start..($end - 1)])
    }
    $n = 0
    foreach ($line in $lines) {
        if ($line -notmatch "(?i)refus(ed|al)") { continue }
        if ($line -match "^\s*#") { continue }                                  # a heading, not a report
        if ($line -match "(?i)\b(nothing|none|no command|no commands|not refused|zero refusals|no refusals|not a refusal|not refusals|non-refusal|neither a refusal)\b") { continue }
        # "Two commands were refused" is two, not one line.
        if ($line -match "(?i)\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+commands?\s+(were|was)\s+refused") {
            $words = @{ one = 1; two = 2; three = 3; four = 4; five = 5; six = 6; seven = 7; eight = 8; nine = 9; ten = 10 }
            $w = $Matches[1].ToLower()
            $n += $(if ($words.ContainsKey($w)) { $words[$w] } else { [int]$w })
            continue
        }
        $n++
    }
    return $n
}

function Build-HandoffMachineBudget {
    <#  The machine-budget paragraph rendered into every brief as
        {{machineBudget}}. Advisory, deliberately: a runner that blocks lanes
        on CPU would deadlock behind another project's compute. Empty when
        the config declares no budget.  #>
    param([hashtable]$Config, [hashtable]$Vars)
    if (-not $Config.ContainsKey("machineBudget") -or -not $Config["machineBudget"]) { return "" }
    $b = $Config["machineBudget"]
    $parts = @("The machine is ONE budget shared by every lane, every subagent and every compute pass.")
    if ($b.ContainsKey("maxCpuPercent") -and $b["maxCpuPercent"]) {
        $parts += "Before any run longer than a minute, read $($Vars['loadFile']) (cpu_percent, running_sessions; the runner refreshes it every poll) and defer while cpu_percent is above $($b['maxCpuPercent'])."
    }
    if ($b.ContainsKey("maxWorkersPerSession") -and $b["maxWorkersPerSession"]) {
        $parts += "Never run more than $($b['maxWorkersPerSession']) worker processes (n_jobs) at once."
    }
    if ($b.ContainsKey("note") -and $b["note"]) { $parts += [string]$b["note"] }
    return ($parts -join " ")
}

function Build-HandoffControllerBrief {
    <#  The orchestrator's own brief, rendered from the same config so a
        controller session can be restarted from one source of truth: where
        the state, log, load and queue files are, and how to reach each lane.  #>
    param([hashtable]$Config)
    $hasFile = $Config.ContainsKey("controllerBriefFile") -and $Config["controllerBriefFile"]
    $hasInline = $Config.ContainsKey("controllerBrief") -and $Config["controllerBrief"]
    if (-not ($hasFile -or $hasInline)) { return "" }
    $stateDir = if ([System.IO.Path]::IsPathRooted($Config["stateDir"])) { $Config["stateDir"] } else { Join-Path $Config["repo"] $Config["stateDir"] }
    $rows = @()
    foreach ($k in ($Config["sessions"].Keys | Sort-Object)) {
        $v = Get-HandoffSessionVars $Config $k $false
        # Per row, because the controller's table is how the operator joins a
        # lane by hand, and a lane on another adapter joins differently.
        $cmd = (Resolve-HandoffSessionLauncher $Config $k $Config["launcher"]).command
        $attachCmd = if ($cmd -eq "agy") { "agy --conversation $($v['rcName'])" }
                     elseif ($cmd -eq "grok") { "grok --resume $($v['rcName'])" }
                     elseif ($cmd -eq "codewhale") { "codewhale exec --resume $($v['rcName'])" }
                     else { "$cmd attach $($v['rcName'])" }
        $rows += "- $k ($($v['sessionName'])): branch $($v['branch']), worktree $($v['worktree']), attach with ``$attachCmd``, DONE note $($v['doneNote'])"
    }
    $vars = @{
        repo = $Config["repo"]; baseBranch = $Config["baseBranch"]; stateDir = $stateDir
        stateFile = (Join-Path $stateDir "state.json"); runnerLog = (Join-Path $stateDir "runner.log")
        loadFile = (Join-Path $stateDir "load.json"); queueDir = (Join-Path $stateDir "queue")
        sessionsTable = ($rows -join "`n")
    }
    if ($Config.ContainsKey("vars") -and $Config["vars"]) {
        foreach ($k in $Config["vars"].Keys) { if (-not $vars.ContainsKey($k)) { $vars[$k] = Expand-HandoffTemplate ([string]$Config["vars"][$k]) $vars } }
    }
    $tpl = if ($hasFile) {
        $tp = if ([System.IO.Path]::IsPathRooted($Config["controllerBriefFile"])) { $Config["controllerBriefFile"] } else { Join-Path $Config["repo"] $Config["controllerBriefFile"] }
        if (-not (Test-Path $tp)) { throw "controllerBriefFile not found: $tp" }
        Get-Content $tp -Raw
    } else { [string]$Config["controllerBrief"] }
    return (Expand-HandoffTemplate $tpl $vars)
}

function Get-HandoffText {
    <#  A native command's captured output as a trimmed string, "" for nothing.

        `([string](git ...)).Trim()` crashed every lane on the first real run
        (2026-09-05): when git prints nothing the cast yields $null, not "",
        and `.Trim()` on $null is "You cannot call a method on a null-valued
        expression". Measured on pwsh 7.5; every `git rev-parse` in the driver
        goes through here.  #>
    param($Value)
    if ($null -eq $Value) { return "" }
    return ([string]::Join("`n", @($Value | ForEach-Object { [string]$_ }))).Trim()
}

function Test-HandoffMergedElsewhere {
    <#  Was this session's branch merged into the base branch by someone else?

        Ancestry alone cannot tell: a branch cut from the base and left with
        NO commits is an ancestor of a base that moved on, exactly like a
        merged branch is - and the first version of this check skipped every
        fresh lane whose base had gained one commit since the cut. The tell is
        the base's FIRST-PARENT line: a `--no-ff` merge leaves the branch tip
        reachable but off that line; a branch merely behind the base sits ON
        it. Tip equal to the base tip is "nothing done yet", never "merged".  #>
    param([string]$Tip, [string]$BaseTip, [bool]$IsAncestor, [string[]]$FirstParentLine)
    if (-not $Tip -or -not $BaseTip) { return $false }
    if ($Tip -eq $BaseTip) { return $false }
    if (-not $IsAncestor) { return $false }
    if (@($FirstParentLine) -contains $Tip) { return $false }
    return $true
}

function Get-HandoffWorktreeAssets {
    <#  -> @{ linkDirs; copyDirs; copyFiles } for one session: the global lists
        followed by the session's own, deduplicated, order preserved.

        Pure, so "which worktree sees the live data directory" is asserted by
        a test rather than discovered by a suite writing into it. A path that
        is both linked and copied is a contradiction and throws here, at
        validation, not at 03:00 inside Ensure-Worktree.  #>
    param([hashtable]$Config, [string]$SessionKey)
    if (-not $Config["sessions"].ContainsKey($SessionKey)) { throw "unknown session '$SessionKey'" }
    $info = $Config["sessions"][$SessionKey]
    $out = @{}
    foreach ($f in @("linkDirs", "copyDirs", "copyFiles")) {
        $merged = @()
        # Plain assignments, never `$x = if (...) { @(...) }`: an if EXPRESSION goes
        # through the output stream, which unrolls a one-element array into a
        # string - and `"a" + @("b")` is then string concatenation ("ab"). The
        # new test caught exactly that on its first run.
        $global = @(); if ($Config.ContainsKey($f) -and $null -ne $Config[$f]) { $global = @($Config[$f]) }
        $own = @();    if ($info.ContainsKey($f) -and $null -ne $info[$f]) { $own = @($info[$f]) }
        foreach ($i in @($global + $own)) {
            if ($null -eq $i) { continue }
            $n = ([string]$i).Trim()
            if ($n -and ($merged -notcontains $n)) { $merged += $n }
        }
        $out[$f] = $merged
    }
    foreach ($d in @($out["linkDirs"])) {
        if (@($out["copyDirs"]) -contains $d) { throw "session '$SessionKey': '$d' is in both linkDirs and copyDirs - pick one" }
    }
    return $out
}

function Build-HandoffResumePrompt {
    <#  The nudge a resumed session receives. It says WHY it is being resumed,
        WHEN it is now, and the one fact a resumed session cannot see for
        itself: every subagent it had in flight died with the previous
        process. Measured 2026-09-08: a host restart killed a session whose
        two fix-round implementers were mid-edit; the generic "continue from
        where you stopped" would have left it waiting for agents that no
        longer existed.  #>
    param([string]$Reason = "", [string]$Now = "", [string]$DoneNote = "")
    $when = if ($Now) { $Now } else { (Get-Date -Format "yyyy-MM-dd HH:mm") }
    $why = switch ($Reason) {
        "limit"     { "the usage limit that stopped you has reset" }
        "transient" { "an API or network failure ended your previous turn" }
        "timeout"   { "your previous turn ran past the session-hours cap and was stopped" }
        "restart"   { "the host (or the runner) restarted and your previous process was killed" }
        default     { "your previous turn ended before the DONE note was written" }
    }
    $parts = @(
        "You are being resumed at $when because $why.",
        "Continue from your last commit; nothing you committed is lost.",
        "Every subagent you had in flight is gone with the previous process: check `git status` for their unfinished, staged or unstaged work, decide whether to finish it or revert it, and re-dispatch what is still needed from your own briefs and review files.",
        "Read your controller inbox before doing anything else."
    )
    if ($DoneNote) { $parts += "If the DONE note ($DoneNote) is not written yet, keep working until it is, then stop." }
    return ($parts -join " ")
}

function Build-HandoffMcpPolicy {
    <#  The paragraph that tells a session HOW to use mandatory MCP servers,
        rendered from config so every worktree session is systematically hooked up
        to Serena, Omnigraph, and Graphify. #>
    param([hashtable]$Config, [hashtable]$Vars)
    $wt = if ($Vars -and $Vars.ContainsKey("worktree") -and $Vars["worktree"]) { [string]$Vars["worktree"] } else { "" }
    $repo = if ($Vars -and $Vars.ContainsKey("repo") -and $Vars["repo"]) { [string]$Vars["repo"] }
            elseif ($Config -and $Config.ContainsKey("repo") -and $Config["repo"]) { [string]$Config["repo"] }
            else { "" }
    $repoFolder = if ($repo) { Split-Path $repo -Leaf } else { "active-project" }
    $parts = @(
        "Mandatory MCP servers & memory protocol:",
        "1. Router: Consult 'skills/repository-index/SKILL.md' first before loading skills.",
        "2. Serena: Activate project on this worktree by absolute path before reading or editing code: call activate_project('$wt'). One active project per session.",
        "3. Omnigraph: Persistent structured memory is mandatory. Recall prior architectural decisions and principles before changing code; persist durable decisions before completing (pinned to OMNIGRAPH_GRAPH_ID='$repoFolder'). Never write project data to the global 'memory' graph.",
        "4. Graphify: For questions spanning multiple files or architectural blast radiuses, inspect and query the linked 'graphify-out/' directory for structural dependency maps."
    )
    return ($parts -join "`n")
}

function Resolve-HandoffSessionLauncher {
    <#  The adapter ONE session drives.

        The config's `launcher` is the default; a session may name its own, because spreading a
        wave across two providers is a quota decision per lane, not per repository — mechanical and
        verification lanes on a second agent, judgement lanes on the first. Pure, so "which agent
        does this session start" is asserted by a test rather than discovered at 03:00.

        $Default keeps the swap the driver performs honest: it sets $Config["launcher"] to the
        resolved adapter for the session it is about to run, so every call site keeps reading
        $cfg.launcher unchanged — and without an explicit default, the NEXT session in that lane
        would inherit the previous one's agent.  #>
    param([hashtable]$Config, [string]$SessionKey, [hashtable]$Default = $null)
    $fallback = if ($Default) { $Default } else { $Config["launcher"] }
    if (-not $Config["sessions"].ContainsKey($SessionKey)) { throw "unknown session '$SessionKey'" }
    $info = $Config["sessions"][$SessionKey]
    if (-not ($info.ContainsKey("launcher") -and $info["launcher"])) { return $fallback }
    $raw = $info["launcher"]
    if ($raw -is [string] -and $raw.Trim()) {
        if (Get-Command Get-AdapterTemplate -ErrorAction SilentlyContinue) {
            return (Get-AdapterTemplate ([string]$raw) -IncludeExperimental)
        }
        return @{ command = [string]$raw }
    }
    if ($raw -is [hashtable]) {
        $base = $fallback
        if ($raw.ContainsKey("adapter") -and $raw["adapter"] -and (Get-Command Get-AdapterTemplate -ErrorAction SilentlyContinue)) {
            $base = Get-AdapterTemplate ([string]$raw["adapter"]) -IncludeExperimental
        }
        $merged = @{}
        foreach ($k in $base.Keys) { $merged[$k] = $base[$k] }
        foreach ($k in $raw.Keys) { if ($k -ne "adapter") { $merged[$k] = $raw[$k] } }
        return $merged
    }
    throw "session '$SessionKey': launcher must be an adapter name or an object"
}

function Get-HandoffSessionVars {
    <#  Every placeholder a brief, guard or hook can reference, for one session.
        Pure so that -EmitBriefs renders exactly what a real run would launch —
        a copy-pasted brief that differs from the launched one is worse than no
        brief at all.  #>
    param([hashtable]$Config, [string]$SessionKey, [bool]$IsFollowUp = $false)
    if (-not $Config["sessions"].ContainsKey($SessionKey)) { throw "unknown session '$SessionKey'" }
    $info = $Config["sessions"][$SessionKey]
    $key = if ($IsFollowUp) { "$SessionKey-followup" } else { $SessionKey }
    $stateDir = if ([System.IO.Path]::IsPathRooted($Config["stateDir"])) { $Config["stateDir"] }
                else { Join-Path $Config["repo"] $Config["stateDir"] }

    $vars = @{
        session        = $SessionKey
        key            = $key
        sessionName    = $info["name"]
        model          = $info["model"]
        worktree       = (Join-Path $Config["worktreeParent"] "$($Config['worktreePrefix'])-$($info['name'])")
        branch         = "$($Config['branchPrefix'])/$($info['name'])"
        repo           = $Config["repo"]
        baseBranch     = $Config["baseBranch"]
        worktreePrefix = $Config["worktreePrefix"]
        stateDir       = $stateDir
        rcName         = "handoff-$key"
        planDir        = ""
        # The folder this skill lives in, so postWorktree can reference the
        # bundled helpers (trust_worktree.py) without a repo-specific path.
        skillDir       = $(if ($Config.ContainsKey("skillDir") -and $Config["skillDir"]) { [string]$Config["skillDir"] } else { "" })
    }
    # Config passthrough may reference the built-ins above, so it is expanded after them.
    if ($Config.ContainsKey("vars") -and $Config["vars"]) {
        foreach ($k in $Config["vars"].Keys) { $vars[$k] = Expand-HandoffTemplate ([string]$Config["vars"][$k]) $vars }
    }
    $vars["loadFile"] = Join-Path $stateDir "load.json"
    $vars["queueDir"] = Join-Path $stateDir "queue"
    # The controller -> session channel. A background session cannot be
    # messaged from a non-interactive controller (no `claude send`; the
    # desktop session tools do not reach `--bg` sessions), so the brief tells
    # the session to READ this file at every decision point and long wait.
    $vars["inbox"] = Join-Path (Join-Path $stateDir "inbox") "$key.md"
    $vars["subagentPolicy"] = Build-HandoffSubagentPolicy $Config $SessionKey
    $vars["mcpPolicy"] = Build-HandoffMcpPolicy $Config $vars
    $vars["traps"] = Build-HandoffTraps $Config
    $vars["machineBudget"] = Build-HandoffMachineBudget $Config $vars
    $vars["doneNote"] = Expand-HandoffTemplate ([string]$Config["doneNote"]) $vars
    return $vars
}

function Build-HandoffBrief {
    <#  Session body (inline or file) expanded, then wrapped in the shared
        briefTemplate. The template is the DRY seam: rules every session obeys
        are written once, and each session file carries only its own task.  #>
    param([hashtable]$Config, [string]$SessionKey, [hashtable]$Vars)
    $info = $Config["sessions"][$SessionKey]
    $v = $Vars.Clone()
    $v["brief"] = Expand-HandoffTemplate (Resolve-HandoffBrief $Config $info) $v
    # Either form of template wraps the brief. A `briefTemplateFile` given
    # WITHOUT an inline `briefTemplate` used to be ignored silently, so every
    # session launched with its bare body and none of the shared rules.
    $hasFile = $Config.ContainsKey("briefTemplateFile") -and $Config["briefTemplateFile"]
    $hasInline = $Config.ContainsKey("briefTemplate") -and $Config["briefTemplate"]
    if ($hasFile -or $hasInline) {
        $tpl = if ($hasFile) {
            $tp = if ([System.IO.Path]::IsPathRooted($Config["briefTemplateFile"])) { $Config["briefTemplateFile"] } else { Join-Path $Config["repo"] $Config["briefTemplateFile"] }
            if (-not (Test-Path $tp)) { throw "briefTemplateFile not found: $tp" }
            Get-Content $tp -Raw
        } else { [string]$Config["briefTemplate"] }
        return Expand-HandoffTemplate $tpl $v
    }
    return $v["brief"]
}

function Export-HandoffBriefs {
    <#  Render every session as copy-pasteable markdown, in lane order, WITHOUT
        launching anything.

        This is what makes one config serve two workflows: an unattended run, and
        a human pasting each brief into an interactive session by hand. Because
        both go through Build-HandoffBrief, the pasted text is byte-identical to
        what the runner would have launched.  #>
    param([hashtable]$Config)
    $out = New-Object System.Text.StringBuilder
    [void]$out.AppendLine("# Handoff briefs — $($Config['worktreePrefix'])")
    [void]$out.AppendLine()
    [void]$out.AppendLine("Generated from ``$($Config['configPath'])``. Lanes run in parallel; sessions inside a lane run in sequence.")
    [void]$out.AppendLine()
    $ctl = Build-HandoffControllerBrief $Config
    if ($ctl) {
        [void]$out.AppendLine("## Controller")
        [void]$out.AppendLine()
        [void]$out.AppendLine('```text')
        [void]$out.AppendLine($ctl.Trim())
        [void]$out.AppendLine('```')
        [void]$out.AppendLine()
    }
    $laneNo = 0
    foreach ($lane in $Config["lanes"]) {
        $laneNo++
        [void]$out.AppendLine("## Lane $laneNo — $($lane -join ' then ')")
        [void]$out.AppendLine()
        foreach ($s in $lane) {
            $v = Get-HandoffSessionVars $Config $s $false
            $brief = Build-HandoffBrief $Config $s $v
            [void]$out.AppendLine("### Session $s — $($v['sessionName'])")
            [void]$out.AppendLine()
            [void]$out.AppendLine("| | |")
            [void]$out.AppendLine("|---|---|")
            [void]$out.AppendLine("| Model | ``$($v['model'])`` |")
            [void]$out.AppendLine("| Branch | ``$($v['branch'])`` |")
            # The session's OWN adapter: a brief that prints the wrong attach
            # command is exactly the drift -EmitBriefs exists to prevent.
            $cmd = (Resolve-HandoffSessionLauncher $Config $s $Config["launcher"]).command
            $attachCmd = if ($cmd -eq "agy") { "agy --conversation $($v['rcName'])" }
                         elseif ($cmd -eq "grok") { "grok --resume $($v['rcName'])" }
                         elseif ($cmd -eq "codewhale") { "codewhale exec --resume $($v['rcName'])" }
                         else { "$cmd attach $($v['rcName'])" }
            [void]$out.AppendLine("| Remote control | ``$($v['rcName'])`` — join with ``$attachCmd`` |")
            if ($v["doneNote"]) { [void]$out.AppendLine("| Done note | ``$($v['doneNote'])`` |") }
            [void]$out.AppendLine()
            [void]$out.AppendLine('```text')
            [void]$out.AppendLine($brief.Trim())
            [void]$out.AppendLine('```')
            [void]$out.AppendLine()
        }
    }
    return $out.ToString()
}

function Read-HandoffConfig {
    <#  Load, default and VALIDATE the config. Validation is not politeness: a
        lane naming a session that does not exist used to fail at 03:00, four
        hours into the run, as an unhandled throw inside a detached child whose
        stdout nobody was reading.  #>
    param([string]$Path, [string]$DefaultRepo = "")
    if (-not (Test-Path $Path)) { throw "handoff config not found: $Path" }
    try { $raw = ConvertTo-HandoffHash (Get-Content $Path -Raw | ConvertFrom-Json) }
    catch { throw "handoff config is not valid JSON: $Path — $($_.Exception.Message)" }
    if ($raw -isnot [hashtable]) { throw "handoff config must be a JSON object: $Path" }

    $cfg = @{}
    foreach ($k in $script:HandoffDefaults.Keys) { $cfg[$k] = $script:HandoffDefaults[$k] }
    foreach ($k in $raw.Keys) { $cfg[$k] = $raw[$k] }

    # The launcher merges KEY BY KEY, or resolves from AgentAdapters registry
    # when specified as a string (e.g., 'agy', 'claude', 'grok', 'codewhale').
    if ($raw.ContainsKey("launcher")) {
        if ($raw["launcher"] -is [string] -and $raw["launcher"].Trim()) {
            if (Get-Command Get-AdapterTemplate -ErrorAction SilentlyContinue) {
                $cfg["launcher"] = Get-AdapterTemplate ([string]$raw["launcher"]) -IncludeExperimental
            } else {
                $cfg["launcher"] = @{ command = [string]$raw["launcher"] }
            }
        }
        elseif ($raw["launcher"] -is [hashtable]) {
            $baseLauncher = $script:HandoffDefaults["launcher"]
            if ($raw["launcher"].ContainsKey("adapter") -and $raw["launcher"]["adapter"]) {
                if (Get-Command Get-AdapterTemplate -ErrorAction SilentlyContinue) {
                    $baseLauncher = Get-AdapterTemplate ([string]$raw["launcher"]["adapter"]) -IncludeExperimental
                }
            }
            $merged = @{}
            foreach ($k in $baseLauncher.Keys) { $merged[$k] = $baseLauncher[$k] }
            foreach ($k in $raw["launcher"].Keys) { $merged[$k] = $raw["launcher"][$k] }
            $cfg["launcher"] = $merged
        }
    }

    # stallWindow merges KEY BY KEY for the same reason the launcher does: a
    # config naming only `maxStalls` would otherwise leave `hours` null, and a
    # null window is silently a zero-hour one - every stall would look like the
    # first, and the rate rule would never fire.
    if ($raw.ContainsKey("stallWindow") -and $raw["stallWindow"] -is [hashtable]) {
        $mergedWindow = @{}
        foreach ($k in $script:HandoffDefaults["stallWindow"].Keys) { $mergedWindow[$k] = $script:HandoffDefaults["stallWindow"][$k] }
        foreach ($k in $raw["stallWindow"].Keys) { $mergedWindow[$k] = $raw["stallWindow"][$k] }
        $cfg["stallWindow"] = $mergedWindow
    }

    # `repo` is optional so a copied config carries no machine-specific path.
    if (-not $cfg.ContainsKey("repo") -or -not $cfg["repo"]) {
        if ($DefaultRepo) { $cfg["repo"] = $DefaultRepo }
        else { throw "config does not set 'repo' and no git repository was detected — run from inside a checkout, or set 'repo' explicitly" }
    }
    $cfg["configPath"] = (Resolve-Path $Path).Path

    if (-not $cfg.ContainsKey("sessions") -or -not $cfg["sessions"] -or $cfg["sessions"].Keys.Count -eq 0) {
        throw "config must define at least one entry under 'sessions'"
    }
    foreach ($key in @($cfg["sessions"].Keys)) {
        $s = $cfg["sessions"][$key]
        if ($s -isnot [hashtable]) { throw "session '$key' must be an object" }
        if (-not $s.ContainsKey("model") -or -not $s["model"]) { throw "session '$key' must set 'model'" }
        if (-not $s.ContainsKey("name") -or -not $s["name"]) { $s["name"] = $key.ToLower() }
        # A guards-only session launches no agent: it cuts a worktree at the
        # base branch's HEAD and runs the guards there - the pinned, idle
        # final suite that every lane's DONE note used to say was "still owed".
        $s["guardsOnly"] = [bool]($s.ContainsKey("guardsOnly") -and $s["guardsOnly"])
        if (-not $s["guardsOnly"] -and -not ($s.ContainsKey("brief") -and $s["brief"]) -and -not ($s.ContainsKey("briefFile") -and $s["briefFile"])) {
            throw "session '$key' must set either 'brief' or 'briefFile'"
        }
        if (-not ($s.ContainsKey("brief") -and $s["brief"]) -and -not ($s.ContainsKey("briefFile") -and $s["briefFile"])) { $s["brief"] = "(guards only)" }
        # `resources`: what the session HOLDS while running, as name[:read|write]
        # (bare name = write). Ordering (dependsOn) is not exclusion: a reader can
        # starve a single-holder store's writer, so the runner refuses to start a
        # session whose resources conflict with a RUNNING one.
        $res = @()
        if ($s.ContainsKey("resources") -and $null -ne $s["resources"]) {
            foreach ($r in @($s["resources"])) { $n = ([string]$r).Trim(); if ($n) { $res += $n } }
        }
        $s["resources"] = $res
        # `dependsOn`: sessions that must have MERGED before this one starts. It
        # is the one cross-lane primitive: a lane holding a dependent session
        # waits (its worktree is cut only once the base branch carries the
        # dependencies) instead of the operator running the runner twice.
        $deps = @()
        if ($s.ContainsKey("dependsOn") -and $null -ne $s["dependsOn"]) {
            foreach ($d in @($s["dependsOn"])) { $n = ([string]$d).Trim(); if ($n) { $deps += $n } }
        }
        $s["dependsOn"] = $deps
        # Per-session worktree assets: what THIS session's worktree links or
        # copies on top of the global lists. Giving every worktree everything
        # is how a suite run in the wrong lane reaches the live data directory,
        # so the data lane alone links `data`, the port lane alone links the
        # prior-art drop, and the rest see neither. Normalised here so the
        # driver never has to ask whether a key exists.
        foreach ($f in @("linkDirs", "copyDirs", "copyFiles")) {
            $items = @()
            if ($s.ContainsKey($f) -and $null -ne $s[$f]) {
                foreach ($i in @($s[$f])) { $n = ([string]$i).Trim(); if ($n) { $items += $n } }
            }
            $s[$f] = $items
        }
        # A per-session launcher is resolved EAGERLY so a typo throws here, at
        # validation, instead of four hours in when that lane's turn comes.
        if ($s.ContainsKey("launcher") -and $s["launcher"]) {
            try { [void](Resolve-HandoffSessionLauncher $cfg $key $cfg["launcher"]) }
            catch { throw "session '$key': $($_.Exception.Message)" }
        }
    }
    foreach ($key in @($cfg["sessions"].Keys)) {
        foreach ($d in @($cfg["sessions"][$key]["dependsOn"])) {
            if ($d -eq $key) { throw "session '$key' dependsOn itself" }
            if (-not $cfg["sessions"].ContainsKey($d)) {
                throw "session '$key' dependsOn unknown session '$d' (known: $(($cfg['sessions'].Keys | Sort-Object) -join ', '))"
            }
        }
        # Throws when one path is both linked and copied for a session.
        [void](Get-HandoffWorktreeAssets $cfg $key)
    }

    # Lanes default to one session per lane: maximum parallelism, which is the
    # right default only because a repo that needs ordering will say so.
    if (-not $cfg.ContainsKey("lanes") -or -not $cfg["lanes"] -or @($cfg["lanes"]).Count -eq 0) {
        $cfg["lanes"] = @(@($cfg["sessions"].Keys | Sort-Object) | ForEach-Object { , @($_) })
    }
    $lanes = @()
    foreach ($lane in @($cfg["lanes"])) {
        $items = if ($lane -is [string]) { $lane -split "," } else { @($lane) }
        $clean = @()
        foreach ($i in $items) {
            $n = ([string]$i).Trim()
            if (-not $n) { continue }
            if (-not $cfg["sessions"].ContainsKey($n)) {
                throw "lane references unknown session '$n' (known: $(($cfg['sessions'].Keys | Sort-Object) -join ', '))"
            }
            $clean += $n
        }
        if ($clean.Count) { $lanes += , $clean }
    }
    if (-not $lanes.Count) { throw "config resolved to zero runnable lanes" }
    $cfg["lanes"] = $lanes

    # Derived defaults that need `repo` to exist first.
    if (-not $cfg.ContainsKey("worktreeParent") -or -not $cfg["worktreeParent"]) {
        $cfg["worktreeParent"] = (Split-Path $cfg["repo"] -Parent)
    }
    if (-not $cfg.ContainsKey("worktreePrefix") -or -not $cfg["worktreePrefix"]) {
        $cfg["worktreePrefix"] = (Split-Path $cfg["repo"] -Leaf)
    }
    if (-not $cfg.ContainsKey("mutexName") -or -not $cfg["mutexName"]) {
        $cfg["mutexName"] = "Global\handoff-$($cfg['worktreePrefix'])"
    }
    return $cfg
}

Export-ModuleMember -Function ConvertTo-HandoffHash, Expand-HandoffTemplate, Classify-HandoffFailure,
Test-HandoffPermissionPrompt, Read-HandoffState, Write-HandoffState, Resolve-HandoffBrief,
Build-HandoffGuardCommand, Read-HandoffConfig, Build-HandoffSubagentPolicy, Build-HandoffMcpPolicy,
Get-HandoffSessionVars, Build-HandoffBrief, Export-HandoffBriefs,
Get-HandoffLinkType, Resolve-HandoffRepoRoot, Build-HandoffLaunchArgs, Resolve-HandoffSessionLauncher,
New-HandoffConfigScaffold,
Get-HandoffDependencyState, Build-HandoffTraps, Test-HandoffDoneQuiet,
Test-HandoffStalled, Test-HandoffStallBudgetExceeded,
Test-HandoffResourceConflict, Get-HandoffRefusalCount, Build-HandoffMachineBudget, Build-HandoffControllerBrief,
Get-HandoffText, Test-HandoffMergedElsewhere, Build-HandoffResumePrompt, Get-HandoffWorktreeAssets,
Redact-HandoffSecrets, Rotate-HandoffLedger, Write-HandoffLedgerEvent, Read-HandoffLedger, Reconcile-HandoffLedger,
Build-HandoffArchiveManifest, Archive-HandoffSession
