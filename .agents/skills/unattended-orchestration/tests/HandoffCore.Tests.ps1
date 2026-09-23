<#
Plain-PowerShell tests for HandoffCore.psm1 — no Pester dependency, so any
machine with pwsh can run the guards that protect this runner.

    pwsh -NoProfile -File skills/unattended-orchestration/tests/HandoffCore.Tests.ps1

Exit code 0 = green. Non-zero = the number of failed assertions.
#>
$ErrorActionPreference = "Stop"
$script:Failures = 0
$script:Ran = 0

function It([string]$name, [scriptblock]$body) {
    $script:Ran++
    try { & $body; Write-Host "  ok   $name" -ForegroundColor Green }
    catch { $script:Failures++; Write-Host "  FAIL $name`n       $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-Equal($expected, $actual, [string]$because = "") {
    if ($expected -ne $actual) { throw "expected '$expected', got '$actual' $because" }
}
function Assert-True($cond, [string]$because = "") {
    if (-not $cond) { throw "expected true $because" }
}
function Assert-Throws([scriptblock]$body, [string]$match) {
    try { & $body } catch {
        if ($_.Exception.Message -match $match) { return }
        throw "threw, but message '$($_.Exception.Message)' does not match '$match'"
    }
    throw "expected a throw matching '$match', but nothing was thrown"
}

$module = Join-Path (Split-Path $PSScriptRoot -Parent) "HandoffCore.psm1"
Import-Module $module -Force
# HandoffCore imports AgentAdapters into its OWN session state and does not re-export it, so a
# suite that wants to build an adapter template itself must import it too (AdapterContract does
# the same). Measured: without this, Get-AdapterTemplate is "not recognized" here while the same
# call inside HandoffCore's own functions resolves.
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) "AgentAdapters.psm1") -Force

Write-Host "`nClassify-HandoffFailure"
# Why these matter: each kind drives a different recovery. Misclassifying a usage
# limit as 'other' burns the night retrying every two minutes against a wall.
It "reads the epoch out of a usage-limit message and waits for the reset" {
    $future = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 3600
    $r = Classify-HandoffFailure "Claude usage limit reached|$future"
    Assert-Equal "limit" $r.kind
    Assert-True ($r.wait -gt 3000 -and $r.wait -le 6 * 3600) "wait should track the reset epoch, got $($r.wait)"
}
It "caps the limit wait at six hours even for an absurd epoch" {
    $far = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 999999
    $r = Classify-HandoffFailure "usage limit reached|$far"
    Assert-Equal (6 * 3600) $r.wait
}
It "falls back to a flat 30 min for a limit with no epoch" {
    $r = Classify-HandoffFailure "429 too many requests"
    Assert-Equal "limit" $r.kind
    Assert-Equal 1800 $r.wait
}
It "classifies auth failures as terminal (wait 0) so the runner stops instead of looping" {
    $r = Classify-HandoffFailure "Failed to authenticate: OAuth session expired"
    Assert-Equal "auth" $r.kind
    Assert-Equal 0 $r.wait
}
It "auth beats limit when a message contains both" {
    # Ordering guard: auth is checked first on purpose; a login failure is not waitable.
    $r = Classify-HandoffFailure "invalid api key - rate limit"
    Assert-Equal "auth" $r.kind
}
It "classifies overload/network as transient with a 5 min wait" {
    foreach ($t in @("529 overloaded", "500 Internal Server Error", "ECONNRESET", "fetch failed")) {
        $r = Classify-HandoffFailure $t
        Assert-Equal "transient" $r.kind "for '$t'"
        Assert-Equal 300 $r.wait "for '$t'"
    }
}
It "classifies anything unrecognised as 'other'" {
    Assert-Equal "other" (Classify-HandoffFailure "the model wrote a file").kind
}
# Measured 2026-09-12 08:41 on a live lane: Antigravity (`agy`) reports quota
# exhaustion as "Individual quota reached ... Resets in 98h52m33s", which carries
# none of the Claude wordings above -- not "limit reached", not "429". A 99-hour
# quota wall was classified `other` and resumed as an early stop, so the lane
# burned eight futile six-minute attempts against a wall instead of sleeping.
It "reads agy's JSON quota wall as a limit and sleeps to the capped reset" {
    $r = Classify-HandoffFailure '{"conversation_id":"conv_01","status":"ERROR","response":"","error":"Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 98h52m33s.","duration_seconds":377.5,"num_turns":1}'
    Assert-Equal "limit" $r.kind
    Assert-Equal 21600 $r.wait "98h52m33s must cap at the same six hours the epoch branch caps at"
}
It "reads the reset out of agy's RESOURCE_EXHAUSTED line instead of the flat 429 wait" {
    # This one already matched "429", so it was classified `limit` -- but with the
    # flat 1800. The reset phrase is the better fact and must win over the flat wait.
    $r = Classify-HandoffFailure 'API error (attempt 3): RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 98h49m37s.'
    Assert-Equal "limit" $r.kind
    Assert-Equal 21600 $r.wait "the parsed reset, capped, not the flat 1800"
}
It "falls back to the flat 30 min for a quota phrase with no reset time" {
    foreach ($t in @("Individual quota reached.", "quota exceeded for this project", "RESOURCE_EXHAUSTED")) {
        $r = Classify-HandoffFailure $t
        Assert-Equal "limit" $r.kind "for '$t'"
        Assert-Equal 1800 $r.wait "for '$t'"
    }
}
It "keeps the epoch form winning when a message carries both an epoch and a quota phrase" {
    # Ordering pin: the epoch is exact, the "Resets in" phrase is coarse, so the
    # quota branch must sit BELOW the epoch branch and never steal it.
    $future = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 3600
    $r = Classify-HandoffFailure "Claude usage limit reached|$future - Individual quota reached. Resets in 98h00m00s."
    Assert-Equal "limit" $r.kind
    Assert-True ($r.wait -gt 3000 -and $r.wait -lt 6 * 3600) "epoch must win, got $($r.wait)"
}
It "does not read an ordinary agy error line as a quota wall" {
    $r = Classify-HandoffFailure '{"status":"ERROR","error":"the model wrote a file and stopped","num_turns":1}'
    Assert-Equal "other" $r.kind
}

Write-Host "`nTest-HandoffPermissionPrompt"
It "detects the prompts a background session cannot answer" {
    foreach ($t in @("Do you want to proceed?", "Do you trust the files", "[y/n]", "Permission required")) {
        Assert-True (Test-HandoffPermissionPrompt $t) "for '$t'"
    }
}
It "does not fire on ordinary prose" {
    Assert-True (-not (Test-HandoffPermissionPrompt "I proceeded to edit the file")) "false positive"
}

It "recognises the MCP-approval dialog and the folder-trust dialog as prompts" {
    # Measured 2026-09-05: three lanes blocked on "New MCP server found in this
    # project: omnigraph" and the runner read it as an ordinary early stop,
    # resuming into the same dialog every two minutes.
    Assert-True (Test-HandoffPermissionPrompt "New MCP server found in this project: omnigraph`nUse this MCP server`nEnter to confirm")
    Assert-True (Test-HandoffPermissionPrompt "Do you trust the files in this folder?")
    Assert-True (-not (Test-HandoffPermissionPrompt "ran 12 tests, all green"))
}

Write-Host "`nExpand-HandoffTemplate"
It "substitutes {{placeholders}} from a hashtable" {
    Assert-Equal "run in C:\wt on b1" (Expand-HandoffTemplate "run in {{worktree}} on {{branch}}" @{ worktree = "C:\wt"; branch = "b1" })
}
It "leaves unknown placeholders untouched rather than emptying them" {
    # An emptied placeholder produces a brief that silently omits the task.
    Assert-Equal "hi {{nope}}" (Expand-HandoffTemplate "hi {{nope}}" @{ other = "x" })
}
It "is not confused by a value that itself contains braces" {
    Assert-Equal "a {{b}} c" (Expand-HandoffTemplate "a {{x}} c" @{ x = "{{b}}" })
}

Write-Host "`nRead-HandoffConfig / validation"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("handoff-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    $good = @{
        repo         = $tmp
        baseBranch   = "main"
        branchPrefix = "wave"
        sessions     = @{ A = @{ name = "alpha"; model = "opus"; brief = "do A" } }
        lanes        = @(, @("A"))
    }
    $goodPath = Join-Path $tmp "good.json"
    $good | ConvertTo-Json -Depth 8 | Set-Content $goodPath -Encoding utf8

    It "loads a minimal valid config and applies defaults" {
        $c = Read-HandoffConfig $goodPath
        Assert-Equal "main" $c.baseBranch
        Assert-Equal "auto" $c.permissionMode "permissionMode should default to auto"
        Assert-True ($c.stateDir) "stateDir should have a default"
        Assert-True ($c.allowedTools.Count -gt 0) "allowedTools should have a default"
    }
    It "defaults the stall watchdog to 60 min and its rate rule to 5 stalls within 3 h" {
        $c = Read-HandoffConfig $goodPath
        Assert-Equal 60 $c.stallMinutes "stallMinutes default"
        Assert-Equal 5 $c.stallWindow.maxStalls "stallWindow.maxStalls default"
        Assert-Equal 3 $c.stallWindow.hours "stallWindow.hours default"
    }
    It "merges stallWindow key by key so a config naming one half keeps the other" {
        # A whole-object override would leave `hours` null, and the session loop
        # multiplies it by 3600: the window would silently become zero.
        $w = $good.Clone(); $w.stallWindow = @{ maxStalls = 3 }
        $p = Join-Path $tmp "stallwindow.json"; $w | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal 3 $c.stallWindow.maxStalls "the config's half must win"
        Assert-Equal 3 $c.stallWindow.hours "the unnamed half must keep its default"
    }
    It "resolves lanes from the session table when lanes are omitted" {
        $noLanes = $good.Clone(); $noLanes.Remove("lanes")
        $p = Join-Path $tmp "nolanes.json"; $noLanes | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal 1 $c.lanes.Count "one lane per session when unspecified"
    }
    It "keeps a multi-session lane as ONE lane, not one lane per session" {
        # PowerShell's pipeline unrolls nested arrays. Losing this collapses
        # 'run E then A in sequence' into 'run E and A at once', which is the
        # exact contention the lane existed to prevent.
        $two = $good.Clone()
        $two.sessions = @{ A = @{ name = "alpha"; model = "opus"; brief = "a" }
                           B = @{ name = "beta";  model = "opus"; brief = "b" } }
        $two.lanes = @(, @("A", "B"))
        $p = Join-Path $tmp "onelane.json"; $two | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal 1 $c.lanes.Count "expected a single lane"
        Assert-Equal "A,B" ($c.lanes[0] -join ",") "expected both sessions in lane 1, in order"
    }
    It "keeps two lanes of two distinct" {
        $four = $good.Clone()
        $four.sessions = @{ A = @{ name = "a"; model = "opus"; brief = "a" }; B = @{ name = "b"; model = "opus"; brief = "b" }
                            C = @{ name = "c"; model = "opus"; brief = "c" }; D = @{ name = "d"; model = "opus"; brief = "d" } }
        $four.lanes = @(@("A", "B"), @("C", "D"))
        $p = Join-Path $tmp "twolanes.json"; $four | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal 2 $c.lanes.Count
        Assert-Equal "A,B" ($c.lanes[0] -join ",")
        Assert-Equal "C,D" ($c.lanes[1] -join ",")
    }
    It "accepts the comma-string lane form too" {
        $cs = $good.Clone()
        $cs.sessions = @{ A = @{ name = "a"; model = "opus"; brief = "a" }; B = @{ name = "b"; model = "opus"; brief = "b" } }
        $cs.lanes = @("A,B")
        $p = Join-Path $tmp "commalane.json"; $cs | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal 1 $c.lanes.Count
        Assert-Equal "A,B" ($c.lanes[0] -join ",")
    }

    Write-Host "`nConvertTo-HandoffHash array nesting"
    It "preserves nested arrays instead of unrolling them through the pipeline" {
        $o = '{"lanes":[["A","B"],["C"]]}' | ConvertFrom-Json
        $h = ConvertTo-HandoffHash $o
        Assert-Equal 2 $h["lanes"].Count "outer array should keep 2 elements"
        Assert-Equal "A,B" ($h["lanes"][0] -join ",")
    }
    It "rejects a config whose lane names a session that does not exist" {
        $bad = $good.Clone(); $bad.lanes = @(, @("A", "ZZ"))
        $p = Join-Path $tmp "badlane.json"; $bad | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "ZZ"
    }
    It "rejects a config with no sessions" {
        $bad = $good.Clone(); $bad.sessions = @{}
        $p = Join-Path $tmp "nosessions.json"; $bad | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "sessions"
    }
    It "rejects a session missing its model" {
        $bad = $good.Clone(); $bad.sessions = @{ A = @{ name = "alpha"; brief = "x" } }
        $p = Join-Path $tmp "nomodel.json"; $bad | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "model"
    }
    It "rejects a missing config file with a path in the message" {
        Assert-Throws { Read-HandoffConfig (Join-Path $tmp "absent.json") } "absent.json"
    }
    It "reads a brief from briefFile when brief is not inline" {
        $bf = Join-Path $tmp "brief-a.md"; "brief body" | Set-Content $bf -Encoding utf8
        $c2 = $good.Clone(); $c2.sessions = @{ A = @{ name = "alpha"; model = "opus"; briefFile = "brief-a.md" } }
        $p = Join-Path $tmp "brieffile.json"; $c2 | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal "brief body" (Resolve-HandoffBrief $c $c.sessions["A"]).Trim()
    }

    Write-Host "`nState round-trip"
    It "writes and reads back state, surviving PS 5.1's lack of -AsHashtable" {
        $sf = Join-Path $tmp "state.json"
        Write-HandoffState $sf @{ A = @{ status = "merged"; session_id = "abc123" } }
        $s = Read-HandoffState $sf
        Assert-Equal "merged" $s["A"]["status"]
        Assert-Equal "abc123" $s["A"]["session_id"]
    }
    It "returns an empty table for a missing or corrupt state file" {
        Assert-Equal 0 (Read-HandoffState (Join-Path $tmp "none.json")).Count
        $bad = Join-Path $tmp "corrupt.json"; "{not json" | Set-Content $bad -Encoding utf8
        Assert-Equal 0 (Read-HandoffState $bad).Count
    }

    Write-Host "`nGuard command construction"
    It "builds the guard command from config, expanding {{guards}} to the path list" {
        $c = Read-HandoffConfig $goodPath
        $c.guards = @{ command = "pytest"; args = @("-q", "{{guards}}"); paths = @("t/a.py", "t/b.py") }
        $cmd = Build-HandoffGuardCommand $c @{}
        Assert-Equal "pytest" $cmd.command
        Assert-Equal "-q,t/a.py,t/b.py" ($cmd.args -join ",")
    }
    It "reports no guards when the config declares none, rather than inventing pytest" {
        $c = Read-HandoffConfig $goodPath
        Assert-True ($null -eq (Build-HandoffGuardCommand $c @{})) "guards must be opt-in"
    }

    Write-Host "`nSession vars and brief assembly"
    It "derives worktree, branch and remote-control name from config" {
        $c = Read-HandoffConfig $goodPath
        $v = Get-HandoffSessionVars $c "A" $false
        Assert-Equal "wave/alpha" $v["branch"]
        Assert-Equal "alpha" $v["sessionName"]
        Assert-Equal "handoff-A" $v["rcName"] "the remote-control name is what an operator types to join"
        Assert-True ($v["worktree"] -like "*-alpha") "worktree should end in the session name, got $($v['worktree'])"
    }
    It "marks a follow-up run with a distinct key and remote-control name" {
        $c = Read-HandoffConfig $goodPath
        $v = Get-HandoffSessionVars $c "A" $true
        Assert-Equal "A-followup" $v["key"]
        Assert-Equal "handoff-A-followup" $v["rcName"] "a follow-up must not collide with the night run"
    }
    It "wraps the session brief in briefTemplate and resolves every placeholder" {
        $c = Read-HandoffConfig $goodPath
        $c.briefTemplate = "PRE {{sessionName}} :: {{brief}} :: POST {{branch}}"
        $v = Get-HandoffSessionVars $c "A" $false
        $b = Build-HandoffBrief $c "A" $v
        Assert-Equal "PRE alpha :: do A :: POST wave/alpha" $b
    }
    It "leaves no unexpanded placeholder in an assembled brief" {
        # An unexpanded {{...}} reaching a session is a brief that reads fine and
        # silently omits its own task.
        $c = Read-HandoffConfig $goodPath
        $c.sessions["A"]["brief"] = "work in {{worktree}} on {{branch}} as {{model}}"
        $c.briefTemplate = "{{brief}} -- done note: {{doneNote}}"
        $b = Build-HandoffBrief $c "A" (Get-HandoffSessionVars $c "A" $false)
        if ($b -match '\{\{\w+\}\}') { throw "unexpanded placeholder in brief: $b" }
    }

    Write-Host "`nBuild-HandoffSubagentPolicy"
    It "returns empty when the config declares no subagent policy" {
        $c = Read-HandoffConfig $goodPath
        Assert-Equal "" (Build-HandoffSubagentPolicy $c) "subagent policy must be opt-in"
    }
    It "states the concurrency cap and both model tiers" {
        $c = Read-HandoffConfig $goodPath
        $c.subagents = @{ maxConcurrent = 2; tiers = @{ mechanical = "sonnet"; judgement = "opus" } }
        $p = Build-HandoffSubagentPolicy $c
        Assert-True ($p -match "at most 2") "must state the cap, got: $p"
        Assert-True ($p -match "sonnet") "must name the mechanical tier"
        Assert-True ($p -match "opus") "must name the judgement tier"
    }
    It "is reachable from a brief as {{subagentPolicy}}" {
        $c = Read-HandoffConfig $goodPath
        $c.subagents = @{ maxConcurrent = 3; tiers = @{ mechanical = "sonnet"; judgement = "opus" } }
        $c.briefTemplate = "{{brief}} || {{subagentPolicy}}"
        $b = Build-HandoffBrief $c "A" (Get-HandoffSessionVars $c "A" $false)
        Assert-True ($b -match "at most 3") "policy must reach the brief, got: $b"
    }

    Write-Host "`nExport-HandoffBriefs"
    It "emits one markdown section per session, in lane order" {
        $two = $good.Clone()
        $two.sessions = @{ A = @{ name = "alpha"; model = "opus";   brief = "do A" }
                           B = @{ name = "beta";  model = "sonnet"; brief = "do B" } }
        $two.lanes = @(, @("B", "A"))
        $p = Join-Path $tmp "export.json"; $two | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $md = Export-HandoffBriefs (Read-HandoffConfig $p)
        $ib = $md.IndexOf("Session B"); $ia = $md.IndexOf("Session A")
        if ($ib -lt 0 -or $ia -lt 0) { throw "both sessions must appear`n$md" }
        if ($ib -gt $ia) { throw "lane order must be preserved (B before A)" }
    }
    It "carries the model, branch and attach command for each session" {
        $c = Read-HandoffConfig $goodPath
        $md = Export-HandoffBriefs $c
        Assert-True ($md -match "opus") "model must be stated"
        Assert-True ($md -match "wave/alpha") "branch must be stated"
        Assert-True ($md -match "handoff-A") "remote-control name must be stated"
    }
    It "fences the brief so it can be copy-pasted whole" {
        $c = Read-HandoffConfig $goodPath
        $md = Export-HandoffBriefs $c
        Assert-True ($md -match '(?s)```text\s*.*do A.*?```') "brief must sit in a fenced block`n$md"
    }

    # ----------------------------------------------------------------------
    # Portability: everything below exists so the skill can be COPIED into an
    # unknown repository and work there without editing the script.
    # ----------------------------------------------------------------------
    Write-Host "`nPortability — repo auto-detection"
    It "does not require 'repo': it falls back to the detected root" {
        $noRepo = $good.Clone(); $noRepo.Remove("repo")
        $p = Join-Path $tmp "norepo.json"; $noRepo | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p -DefaultRepo $tmp
        Assert-Equal $tmp $c.repo "an absent repo must resolve to the detected root"
    }
    It "still honours an explicit 'repo' over the detected root" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo "C:\somewhere\else"
        Assert-Equal $tmp $c.repo "explicit config must win"
    }
    It "fails with an actionable message when neither is available" {
        $noRepo = $good.Clone(); $noRepo.Remove("repo")
        $p = Join-Path $tmp "norepo2.json"; $noRepo | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "repo"
    }
    It "detects this checkout's root from a path inside it" {
        $root = Resolve-HandoffRepoRoot $PSScriptRoot
        Assert-True ($root) "should find a git root from the test directory"
        Assert-True (Test-Path (Join-Path $root ".git")) "detected root must contain .git, got $root"
    }

    Write-Host "`nPortability — launcher adapter"
    It "defaults to a Claude Code adapter so an unedited config just works" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        Assert-Equal "claude" $c.launcher.command
    }
    It "puts the tool list BEFORE the prompt in the default start template" {
        # The gotcha this encodes: --allowedTools is variadic and swallows a prompt
        # placed after it, so the session starts with no task at all.
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $tpl = @($c.launcher.start) -join " "
        $iTools = $tpl.IndexOf("{{tools}}"); $iPrompt = $tpl.IndexOf("{{prompt}}")
        Assert-True ($iTools -ge 0 -and $iPrompt -ge 0) "template must use both markers: $tpl"
        Assert-True ($iTools -lt $iPrompt) "{{tools}} must precede {{prompt}}, got: $tpl"
    }
    It "expands {{tools}} in place into the flag, the tools, and the permission mode" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $c.allowedTools = @("Read", "Edit"); $c.permissionMode = "auto"
        $a = Build-HandoffLaunchArgs $c "start" @{ rcName = "rc1"; model = "opus"; prompt = "DO IT" }
        $s = $a -join " "
        Assert-True ($s -match "--allowedTools Read Edit") "tools must expand in place, got: $s"
        Assert-True ($s -match "--permission-mode auto") "permission mode must follow the tools"
        Assert-Equal "DO IT" $a[-1] "the prompt must be the LAST argument"
    }
    It "keeps a multi-word prompt as ONE argument, never split on spaces" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $a = Build-HandoffLaunchArgs $c "start" @{ rcName = "rc1"; model = "opus"; prompt = "a b c" }
        Assert-Equal "a b c" $a[-1]
        Assert-Equal 1 (@($a | Where-Object { $_ -eq "a b c" }).Count)
    }
    It "renders the resume action with the session id" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $a = Build-HandoffLaunchArgs $c "resume" @{ sessionId = "sid-9"; prompt = "carry on" }
        Assert-True (($a -join " ") -match "sid-9") "resume must carry the session id"
    }
    It "lets a repository swap in a different agent entirely" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $c.launcher = @{ command = "my-agent"; start = @("run", "--name", "{{rcName}}", "{{prompt}}") }
        $a = Build-HandoffLaunchArgs $c "start" @{ rcName = "z"; prompt = "task" }
        Assert-Equal "my-agent" $c.launcher.command
        Assert-Equal "run --name z task" ($a -join " ")
    }
    It "throws on an action the launcher does not define, naming it" {
        $c = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $c.launcher = @{ command = "x"; start = @("go") }
        Assert-Throws { Build-HandoffLaunchArgs $c "logs" @{} } "logs"
    }

    Write-Host "`nPortability — cross-platform primitives"
    It "picks Junction on Windows and SymbolicLink elsewhere" {
        $expected = if ($IsWindows -or $null -eq $IsWindows) { "Junction" } else { "SymbolicLink" }
        Assert-Equal $expected (Get-HandoffLinkType)
    }
    It "scopes the mutex name per repository so two repos never block each other" {
        $c1 = Read-HandoffConfig $goodPath -DefaultRepo $tmp
        $other = $good.Clone(); $other.repo = "C:\other\repo-two"; $other.Remove("mutexName")
        $p = Join-Path $tmp "other.json"; $other | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c2 = Read-HandoffConfig $p -DefaultRepo $tmp
        Assert-True ($c1.mutexName -ne $c2.mutexName) "distinct repos must get distinct mutex names"
    }

    Write-Host "`nCross-lane dependencies (dependsOn)"
    # Why these matter: a dependent session that starts before its dependency
    # merged forks from a base branch WITHOUT that work; one that waits for a
    # dependency that can never merge polls until morning. Both are silent.
    It "reports ready when every dependency has merged (or was left unmerged on purpose)" {
        $st = @{ A = @{ status = "merged" }; B = @{ status = "done-unmerged" } }
        $r = Get-HandoffDependencyState $st @("A", "B")
        Assert-True $r.ready "merged + done-unmerged must satisfy"
        Assert-Equal 0 (@($r.waiting).Count)
    }
    It "waits on a dependency that is running or has no state yet" {
        $st = @{ A = @{ status = "running" } }
        $r = Get-HandoffDependencyState $st @("A", "NOTYET")
        Assert-True (-not $r.ready)
        Assert-Equal "A,NOTYET" (@($r.waiting) -join ",")
        Assert-Equal 0 (@($r.failed).Count)
    }
    It "fails the dependent on a terminal, non-merged dependency instead of waiting forever" {
        foreach ($bad in @("blocked", "failed", "crashed", "auth-failed", "dependency-failed", "stopped-by-operator")) {
            $st = @{ A = @{ status = $bad } }
            $r = Get-HandoffDependencyState $st @("A")
            Assert-True (-not $r.ready) "status $bad must not be ready"
            Assert-Equal "A" (@($r.failed) -join ",") "status $bad must be reported as failed"
        }
    }
    It "keeps waiting on a red guard or a merge conflict, which an operator resolves by hand" {
        # Measured 2026-09-05: three of nine merges needed a hand resolution, each done within
        # minutes, and the final-suite lane failed and had to be re-queued after every one.
        foreach ($fixable in @("guards-red", "merge-conflict")) {
            $st = @{ A = @{ status = $fixable } }
            $r = Get-HandoffDependencyState $st @("A")
            Assert-True (-not $r.ready) "status $fixable must not be ready"
            Assert-Equal "A" (@($r.waiting) -join ",") "status $fixable must be waited on, not failed"
            Assert-Equal 0 (@($r.failed).Count)
        }
    }
    It "normalises dependsOn to an array and rejects unknown or self references" {
        $dep = $good.Clone()
        $dep.sessions = @{ A = @{ name = "alpha"; model = "opus"; brief = "a" }
                           B = @{ name = "beta";  model = "opus"; brief = "b"; dependsOn = "A" } }
        $dep.lanes = @(@("A"), @("B"))
        $p = Join-Path $tmp "deps.json"; $dep | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-Equal "A" (@($c.sessions.B.dependsOn) -join ",") "a bare string must become a one-element array"
        Assert-Equal 0 (@($c.sessions.A.dependsOn).Count) "a session without dependsOn gets an empty array"

        $dep.sessions.B.dependsOn = @("ZZ")
        $dep | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "ZZ"

        $dep.sessions.B.dependsOn = @("B")
        $dep | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "itself"
    }
    It "exposes skillDir to briefs and hooks when the runner sets it" {
        $c = Read-HandoffConfig $goodPath
        $c["skillDir"] = "X:\\the\\skill"
        $v = Get-HandoffSessionVars $c "A" $false
        Assert-Equal "X:\\the\\skill" $v["skillDir"]
        Assert-Equal "X:\\the\\skill/trust_worktree.py" (Expand-HandoffTemplate "{{skillDir}}/trust_worktree.py" $v)
    }

    Write-Host "`nbriefTemplateFile, traps, and the DONE-note finish rule"
    It "wraps the brief with briefTemplateFile even when no inline briefTemplate is set" {
        # Measured 2026-09-05: a config with only briefTemplateFile launched every
        # session with its bare body - none of the shared rules reached them.
        $tplPath = Join-Path $tmp "tpl.md"
        "PREAMBLE for {{session}}`n{{brief}}`nPOSTAMBLE" | Set-Content $tplPath -Encoding utf8
        $t = $good.Clone(); $t.briefTemplateFile = "tpl.md"
        $p = Join-Path $tmp "tplfile.json"; $t | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        $v = Get-HandoffSessionVars $c "A" $false
        $b = Build-HandoffBrief $c "A" $v
        Assert-True ($b -match "PREAMBLE for A") "template file must wrap the brief"
        Assert-True ($b -match "do A") "the session body must be inside"
        Assert-True ($b -match "POSTAMBLE") "the template's tail must survive"
    }
    It "renders the traps list into every brief, and nothing when unset" {
        $c = Read-HandoffConfig $goodPath
        Assert-Equal "" (Build-HandoffTraps $c) "no traps configured -> empty block"
        $c["traps"] = @("first trap", "second trap")
        $block = Build-HandoffTraps $c
        Assert-True ($block -match "first trap" -and $block -match "second trap")
        $v = Get-HandoffSessionVars $c "A" $false
        Assert-True ($v["traps"] -match "second trap") "traps must reach the session vars"
    }
    It "calls a session finished only when the DONE note exists AND the clean branch has been quiet" {
        $now = 1000000
        Assert-True (-not (Test-HandoffDoneQuiet $false ($now - 3600) $now 20 $false)) "no DONE note -> not finished"
        Assert-True (-not (Test-HandoffDoneQuiet $true ($now - 60) $now 20 $false)) "a commit one minute ago -> still working"
        Assert-True (-not (Test-HandoffDoneQuiet $true ($now - 3600) $now 20 $true)) "uncommitted changes -> still working"
        Assert-True (-not (Test-HandoffDoneQuiet $true 0 $now 20 $false)) "no commit at all -> not finished"
        Assert-True (Test-HandoffDoneQuiet $true ($now - 3600) $now 20 $false) "DONE note + clean + quiet an hour -> finished"
    }

    Write-Host "`nStall watchdog (a hung child, not a finished one)"
    # Why these matter: quietMinutes only ever closes a session that ALREADY wrote
    # its DONE note. A child that hangs before writing one is invisible to it, and
    # the lane then waits for the launcher's own print-timeout (agy: 360 min).
    # Measured 2026-09-12. The opposite error is worse: the same day, an agy child
    # that looked identical from outside - empty stdout, no commit, no DONE note,
    # 35 min of it - was working the whole time and finished normally, so all
    # THREE signals must be quiet before the runner ends a child.
    It "calls a session stalled only when its log, its worktree AND its process tree are all quiet" {
        $now = 1000000
        $old = $now - 3600      # an hour of silence
        $recent = $now - 300    # five minutes ago
        Assert-True (Test-HandoffStalled $old $old $old $false $now 30) "all three quiet, no DONE note -> stalled"
        Assert-True (-not (Test-HandoffStalled $old $recent $old $false $now 30)) "a commit 5 min ago -> working"
        Assert-True (-not (Test-HandoffStalled $recent $old $old $false $now 30)) "a log line 5 min ago -> working"
        Assert-True (-not (Test-HandoffStalled $old $old $recent $false $now 30)) "CPU in its process tree 5 min ago -> working, whatever its logs say"
    }
    It "leaves a session with a DONE note to quietMinutes, and never fires when stallMinutes is 0" {
        $now = 1000000
        $old = $now - 3600
        Assert-True (-not (Test-HandoffStalled $old $old $old $true $now 30)) "DONE note present -> quietMinutes' case, not a stall"
        Assert-True (-not (Test-HandoffStalled $old $old $old $false $now 0)) "stallMinutes 0 disables the watchdog"
    }
    It "treats an unknown timestamp as no evidence rather than as silence" {
        # A signal the runner could not read (no log file yet; no pid to sum CPU
        # over, which is every launcher that is not runner-managed) must not be
        # counted as proof of a hang.
        $now = 1000000
        $old = $now - 3600
        Assert-True (-not (Test-HandoffStalled 0 $old $old $false $now 30)) "no log timestamp -> unknown, not stalled"
        Assert-True (-not (Test-HandoffStalled $old 0 $old $false $now 30)) "no worktree timestamp -> unknown, not stalled"
        Assert-True (-not (Test-HandoffStalled $old $old 0 $false $now 30)) "no CPU reading -> unknown, not stalled"
    }
    It "stops the lane only when the stalls cluster inside the window, not when they are spread out" {
        # The operator's rule: five stalls in about three hours is a general
        # problem; five spread across a night is five one-offs, each resumable.
        $now = 1000000
        # Each element is parenthesised on purpose: PowerShell's comma binds
        # tighter than its minus, so `@($now - 9000, $now - 7200)` subtracts an
        # ARRAY and throws op_Subtraction.
        $four = @(($now - 9000), ($now - 7200), ($now - 5400), ($now - 3600))
        Assert-True (-not (Test-HandoffStallBudgetExceeded $four $now 5 3)) "4 stalls in 3 h -> resume"
        $five = @($four) + ($now - 1800)
        Assert-True (Test-HandoffStallBudgetExceeded $five $now 5 3) "5 stalls in 3 h -> a general problem"
        $spread = @(($now - 14400), ($now - 12000), ($now - 9600), ($now - 7200), ($now - 4800))
        Assert-True (-not (Test-HandoffStallBudgetExceeded $spread $now 5 3)) "5 stalls over 4 h -> the oldest two drop out of the window"
        Assert-True (-not (Test-HandoffStallBudgetExceeded @() $now 5 3)) "no stalls -> nothing to exceed"
    }

    Write-Host "`nResources, refusals, guards-only sessions, controller brief"
    It "write excludes everything on a resource name; read excludes only write" {
        $held = @(@{ key = "W"; resources = @("store:write") }, @{ key = "R"; resources = @("store:read") }, @{ key = "X"; resources = @("other") })
        Assert-Equal "W,R" (@(Test-HandoffResourceConflict $held @("store")) -join ",") "a bare name is a write and conflicts with both"
        Assert-Equal "W" (@(Test-HandoffResourceConflict $held @("store:read")) -join ",") "a read conflicts with the writer only"
        Assert-Equal 0 (@(Test-HandoffResourceConflict $held @("elsewhere:write")).Count) "an unrelated name never conflicts"
        Assert-Equal 0 (@(Test-HandoffResourceConflict @() @("store")).Count) "nothing running -> nothing conflicts"
    }
    It "counts reported refusals and skips the sentences that say nothing was refused" {
        $note = "## Refused`n- 14:21 Stop-Process ... refused as a whole`n- 18:02 prune --apply was refused`nNothing else was refused this session.`n"
        Assert-Equal 2 (Get-HandoffRefusalCount $note)
        Assert-Equal 0 (Get-HandoffRefusalCount "**Nothing was refused by the auto-mode classifier this session.**")
        Assert-Equal 0 (Get-HandoffRefusalCount "")
    }
    It "reads only the Refused section when the note has one, so 'the engine refused the row' in Evidence is not a classifier refusal" {
        $note = "## Landed`n- the importer refused a slash date`n## Evidence`n- test_a_blank_unit_is_refused`n- a refusal is shown in words`n## Refused`nNothing was refused. No command was blocked.`n## Omnigraph`n- Rule refuse-rather-than-estimate`n"
        Assert-Equal 0 (Get-HandoffRefusalCount $note) "prose about the application refusing input is not a classifier refusal"
        $note2 = "## Landed`n- the importer refused a slash date`n## Refused`n- 15:12 purge --apply was refused by the classifier`n`n### Not refused`n- the backfill half ran`n## Omnigraph`n"
        Assert-Equal 1 (Get-HandoffRefusalCount $note2) "one refusal inside the section; a ### subheading does not end it"
        $note3 = "## Refused`n`nTwo commands were refused by the auto-mode permission classifier. Neither was worked around.`n`n1. purge --apply`n2. Stop-Process`n## Omnigraph`n"
        Assert-Equal 2 (Get-HandoffRefusalCount $note3) "a counted sentence contributes its number"
        $note4 = "## Refused`nNothing was refused by the permission classifier.`n`nTooling note, not a refusal: the MCP servers failed to start.`nTwo tool-level failures, neither a refusal: serena never connected.`n## Omnigraph`n"
        Assert-Equal 0 (Get-HandoffRefusalCount $note4) "'not a refusal' and 'neither a refusal' are disclaimers"
    }
    It "accepts a guards-only session with no brief, normalises resources, and lets guards-green satisfy a dependency" {
        $g = $good.Clone()
        $g.sessions = @{ A = @{ name = "alpha"; model = "opus"; brief = "a"; resources = "store:write" }
                         F = @{ name = "final"; model = "sonnet"; guardsOnly = $true; dependsOn = @("A") } }
        $g.lanes = @(@("A"), @("F"))
        $p = Join-Path $tmp "guardsonly.json"; $g | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        Assert-True $c.sessions.F.guardsOnly "guardsOnly must be a bool true"
        Assert-True (-not $c.sessions.A.guardsOnly) "default is false"
        Assert-Equal "store:write" (@($c.sessions.A.resources) -join ",") "a bare string becomes a one-element array"
        Assert-Equal 0 (@($c.sessions.F.resources).Count)
        $r = Get-HandoffDependencyState @{ A = @{ status = "guards-green" } } @("A")
        Assert-True $r.ready "a guards-only session that went green satisfies its dependents"
    }
    It "renders the machine budget into the brief vars only when configured" {
        $c = Read-HandoffConfig $goodPath
        $v = Get-HandoffSessionVars $c "A" $false
        Assert-Equal "" $v["machineBudget"]
        Assert-True ($v["loadFile"] -match "load\.json$") "loadFile must point into the state dir"
        Assert-True ($v["inbox"] -match "inbox[\\/]A\.md$") "each session gets its own inbox file under the state dir"
        $c["machineBudget"] = @{ maxCpuPercent = 75; maxWorkersPerSession = 8 }
        $v = Get-HandoffSessionVars $c "A" $false
        Assert-True ($v["machineBudget"] -match "above 75" -and $v["machineBudget"] -match "8 worker") "budget numbers must reach the brief"
    }
    It "emits a controller brief with every session's attach name when configured" {
        $c = Read-HandoffConfig $goodPath
        Assert-Equal "" (Build-HandoffControllerBrief $c) "no controller brief by default"
        $c["controllerBrief"] = "You watch {{stateFile}} and {{runnerLog}}. Lanes:`n{{sessionsTable}}"
        $b = Build-HandoffControllerBrief $c
        Assert-True ($b -match "state\.json" -and $b -match "runner\.log") "state and log paths must be filled in"
        Assert-True ($b -match "claude attach handoff-A") "each session's attach command must be listed"
        $md = Export-HandoffBriefs $c
        Assert-True ($md -match "## Controller") "-EmitBriefs must include the controller section"
    }

    Write-Host "`nTest-HandoffMergedElsewhere"
    It "treats a branch that merely fell behind the base as NOT merged, and a --no-ff-merged one as merged" {
        # Measured 2026-09-05: the ancestry-only check would have skipped every
        # fresh lane after one commit landed on the base behind its cut.
        $line = @("c3", "c2", "c1", "c0")          # the base's first-parent history, newest first
        Assert-True (-not (Test-HandoffMergedElsewhere "c2" "c3" $true $line)) "behind the base, on its first-parent line -> not merged"
        Assert-True (-not (Test-HandoffMergedElsewhere "c3" "c3" $true $line)) "equal to the base tip -> nothing done yet"
        Assert-True (Test-HandoffMergedElsewhere "b7" "c3" $true $line) "reachable but off the first-parent line -> merged by a --no-ff merge"
        Assert-True (-not (Test-HandoffMergedElsewhere "b7" "c3" $false $line)) "not reachable at all -> not merged"
        Assert-True (-not (Test-HandoffMergedElsewhere "" "c3" $true $line)) "no branch yet -> not merged"
    }

    Write-Host "`nBuild-HandoffResumePrompt"
    It "tells a resumed session why, when, and that its subagents are gone" {
        $p = Build-HandoffResumePrompt -Reason "restart" -Now "2026-09-08 08:40" -DoneNote "x/DONE.md"
        Assert-True ($p -match "2026-09-08 08:40") "the resume time must be stated"
        Assert-True ($p -match "restarted") "a restart must be named as the reason"
        Assert-True ($p -match "subagent") "the session must be told its subagents died"
        Assert-True ($p -match "x/DONE.md") "the DONE note path must be named"
        Assert-True ($p -match "inbox") "the session must be sent to its inbox first"
        $q = Build-HandoffResumePrompt -Reason "limit" -Now "t"
        Assert-True ($q -match "usage limit") "a limit resume names the limit"
        Assert-True ((Build-HandoffResumePrompt -Now "t") -match "before the DONE note") "an early stop is the default reason"
    }

    Write-Host "`nGet-HandoffText (native output to string)"
    It "turns an empty native-command output into an empty string, never null" {
        # A [string] cast of $null gave $null and .Trim() then crashed every
        # lane on the first real run of dependsOn; this is the regression guard.
        $t = Get-HandoffText $null
        Assert-True ($null -ne $t) "must be a string"
        Assert-Equal "" $t
        Assert-Equal "abc" (Get-HandoffText @(" abc ", ""))
        Assert-Equal "a`nb" (Get-HandoffText @("a", "b"))
        Assert-Equal "x" (Get-HandoffText "x")
    }

    Write-Host "`nPortability — scaffolding"
    It "generates a config that Read-HandoffConfig accepts unedited" {
        $dest = Join-Path $tmp "scaffold.json"
        New-HandoffConfigScaffold -Path $dest -RepoRoot $tmp -BaseBranch "main" | Out-Null
        Assert-True (Test-Path $dest) "scaffold must write the file"
        $c = Read-HandoffConfig $dest -DefaultRepo $tmp
        Assert-True ($c.sessions.Keys.Count -ge 1) "scaffold must include a runnable example session"
        Assert-Equal "main" $c.baseBranch
    }
    It "refuses to overwrite an existing config unless forced" {
        $dest = Join-Path $tmp "scaffold2.json"
        New-HandoffConfigScaffold -Path $dest -RepoRoot $tmp -BaseBranch "main" | Out-Null
        Assert-Throws { New-HandoffConfigScaffold -Path $dest -RepoRoot $tmp -BaseBranch "main" } "exists"
        New-HandoffConfigScaffold -Path $dest -RepoRoot $tmp -BaseBranch "main" -Force | Out-Null
    }
    Write-Host "`nGet-HandoffWorktreeAssets (per-session worktree assets)"
    # Why this matters: every worktree that links the live data directory lets a
    # suite run reach real data. The data lane alone links it; the port lane alone
    # links the prior-art drop; everyone else must see neither.
    It "merges global and per-session link/copy lists, deduplicated, and gives other sessions only the global ones" {
        $cfgA = @{
            repo = $tmp; baseBranch = "main"
            copyDirs = @("graphify-out"); copyFiles = @(".env")
            sessions = @{
                A = @{ name = "data";  model = "opus"; brief = "a"; linkDirs = @("data", "data"); copyFiles = @(".env", ".secrets") }
                B = @{ name = "views"; model = "opus"; brief = "b" }
            }
            lanes = @(@("A"), @("B"))
        }
        $p = Join-Path $tmp "assets.json"; $cfgA | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        $c = Read-HandoffConfig $p
        $a = Get-HandoffWorktreeAssets $c "A"
        Assert-Equal "data" (@($a.linkDirs) -join ",") "A links data exactly once"
        Assert-Equal "graphify-out" (@($a.copyDirs) -join ",") "the global copy list reaches A"
        Assert-Equal ".env,.secrets" (@($a.copyFiles) -join ",") "global first, then the session's own, no duplicates"
        $b = Get-HandoffWorktreeAssets $c "B"
        Assert-Equal "" (@($b.linkDirs) -join ",") "B must NOT see the data link"
        Assert-Equal ".env" (@($b.copyFiles) -join ",") "B gets only the global file"
    }
    It "refuses a path that is both linked and copied for one session, at validation time" {
        $bad = @{
            repo = $tmp; baseBranch = "main"; copyDirs = @("graphify-out")
            sessions = @{ A = @{ name = "a"; model = "opus"; brief = "a"; linkDirs = @("graphify-out") } }
            lanes = @(, @("A"))
        }
        $p = Join-Path $tmp "assets-bad.json"; $bad | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "both linkDirs and copyDirs"
    }

    It "writes no absolute machine-specific path into the scaffold" {
        # A scaffold carrying the generating machine's paths is not portable.
        $dest = Join-Path $tmp "scaffold3.json"
        New-HandoffConfigScaffold -Path $dest -RepoRoot $tmp -BaseBranch "main" | Out-Null
        $text = Get-Content $dest -Raw
        Assert-True ($text -notmatch [regex]::Escape($tmp)) "scaffold must not hardcode the repo path; it is auto-detected"
    }

    Write-Host "`nPer-session launcher"
    It "falls back to the config's launcher for a session that names none" {
        $c = @{ launcher = @{ command = "claude" }
                sessions = @{ A = @{ name = "a"; model = "opus"; brief = "x" } } }
        Assert-Equal "claude" (Resolve-HandoffSessionLauncher $c "A").command
    }
    It "resolves a session's adapter NAME to the registered adapter" {
        $c = @{ launcher = @{ command = "claude" }
                sessions = @{ A = @{ name = "a"; model = "m"; brief = "x"; launcher = "agy" } } }
        $l = Resolve-HandoffSessionLauncher $c "A"
        Assert-Equal "agy" $l.command
        Assert-Equal "runner-managed" $l.backgroundMode
    }
    It "merges a session's launcher OBJECT over the adapter it names" {
        $c = @{ launcher = @{ command = "claude" }
                sessions = @{ A = @{ name = "a"; model = "m"; brief = "x"
                                     launcher = @{ adapter = "agy"; probeModel = "gemini-probe" } } } }
        $l = Resolve-HandoffSessionLauncher $c "A"
        Assert-Equal "agy" $l.command
        Assert-Equal "gemini-probe" $l.probeModel
    }
    It "never lets one session's launcher leak into the next" {
        # The driver swaps the resolved launcher into $cfg per session; without an explicit
        # default the session AFTER an agy session would inherit agy silently.
        $c = @{ launcher = @{ command = "claude" }
                sessions = @{ A = @{ name = "a"; model = "m"; brief = "x"; launcher = "agy" }
                              B = @{ name = "b"; model = "m"; brief = "y" } } }
        $global = $c["launcher"]
        $c["launcher"] = Resolve-HandoffSessionLauncher $c "A" $global
        Assert-Equal "claude" (Resolve-HandoffSessionLauncher $c "B" $global).command
    }
    It "rejects an unknown per-session adapter at validation, naming the session" {
        $bad = @{ repo = $tmp; sessions = @{ A = @{ name = "a"; model = "m"; brief = "x"
                                                    launcher = "nosuchagent" } } }
        $p = Join-Path $tmp "badlauncher.json"
        $bad | ConvertTo-Json -Depth 8 | Set-Content $p -Encoding utf8
        Assert-Throws { Read-HandoffConfig $p } "session 'A'.*nosuchagent"
    }
    It "picks the leaf-enforcement flag from the SESSION's adapter, not the global one" {
        $c = @{ launcher = (Get-AdapterTemplate "claude")
                subagents = @{ maxConcurrent = 2; maxDepth = 2; leafRule = $true
                               tiers = @{ mechanical = "sonnet"; judgement = "opus" } }
                sessions = @{ A = @{ name = "a"; model = "m"; brief = "x"; launcher = "agy" } } }
        $policy = Build-HandoffSubagentPolicy $c "A"
        if ($policy -match "disallowedTools") { throw "claude's leaf flag must not reach an agy session" }
        Assert-True ($policy -match "leaves") "the leaf rule itself must still be stated"
    }
}
finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }

Write-Host "`n$($script:Ran) assertions, $($script:Failures) failed."
exit $script:Failures
