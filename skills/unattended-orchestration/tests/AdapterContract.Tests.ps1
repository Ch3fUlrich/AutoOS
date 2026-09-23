<#
    AdapterContract.Tests.ps1 — Contract-Test Harness for Unattended Orchestration Adapters

    Validates that every agent adapter adheres to the launcher contract, generates
    valid argument vectors without token leaks, respects background modes, and
    enforces subagent delegation depth and prompt synchronisation.

        pwsh -NoProfile -File skills/unattended-orchestration/tests/AdapterContract.Tests.ps1
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
function Assert-True([bool]$condition, [string]$because = "") {
    if (-not $condition) { throw "expected true $because" }
}
function Assert-Match([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -notmatch $pattern) { throw "expected output matching '$pattern' $because`n--- got ---`n$text" }
}
function Assert-NotMatch([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -match $pattern) { throw "did NOT expect '$pattern' $because`n--- got ---`n$text" }
}

$coreModule = Join-Path (Split-Path $PSScriptRoot -Parent) "HandoffCore.psm1"
$adapterModule = Join-Path (Split-Path $PSScriptRoot -Parent) "AgentAdapters.psm1"
Import-Module $coreModule -Force
Import-Module $adapterModule -Force

$repoRoot = (Resolve-HandoffRepoRoot)
# The skill's OWN repository, resolved from this file instead of from the caller's working
# directory. The scaffold test below asserts that prompts/unattended-orchestration.md is inlined
# "when present", and the copy that is present is this repository's. Deriving that root from the
# cwd made the precondition depend on where pwsh happened to be launched: the suite passed when
# run from agent-skills and failed from any adopting checkout that ran it from its own root
# (measured 2026-09-12 -- 18 ran / 1 failed from downstream project A, 18 ran / 0 failed from here, same
# commit). A test must bring its own fixture, not inherit one from the shell.
$skillRepoRoot = (Resolve-HandoffRepoRoot (Split-Path $PSScriptRoot -Parent))
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("handoff-adapter-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    Write-Host "`nAdapter Registry & Schema Completeness"
    It "registers stable adapters (claude, agy) and experimental ones (grok, codewhale)" {
        $stable = Get-AdapterNames
        Assert-True ($stable -contains "claude") "stable must include claude"
        Assert-True ($stable -contains "agy") "stable must include agy"
        Assert-True (-not ($stable -contains "grok")) "grok is experimental by default"
        Assert-True (-not ($stable -contains "codewhale")) "codewhale is experimental by default"

        $all = Get-AdapterNames -IncludeExperimental
        Assert-True ($all -contains "grok") "all must include grok"
        Assert-True ($all -contains "codewhale") "all must include codewhale"
    }

    It "resolves aliases correctly (antigravity -> agy, deepseek -> codewhale)" {
        Assert-Equal "agy" (Resolve-AdapterName "antigravity")
        Assert-Equal "agy" (Resolve-AdapterName "gemini")
        Assert-Equal "codewhale" (Resolve-AdapterName "deepseek")
        Assert-Equal "claude" (Resolve-AdapterName "claude-code")
    }

    It "refuses experimental adapters unless explicitly requested" {
        $failed = $false
        try { Get-AdapterTemplate "grok" | Out-Null } catch { $failed = $true }
        Assert-True $failed "must throw when requesting grok without -IncludeExperimental"

        $grok = Get-AdapterTemplate "grok" -IncludeExperimental
        Assert-Equal "grok" $grok.command
    }

    It "verifies schema completeness for every registered adapter" {
        $all = Get-AdapterNames -IncludeExperimental
        foreach ($name in $all) {
            $a = Get-AdapterTemplate $name -IncludeExperimental
            Assert-True ($null -ne $a.command -and $a.command.Trim().Length -gt 0) "$name must define command"
            Assert-True ($a.start -is [array] -and $a.start.Count -gt 0) "$name must define start array"
            Assert-True ($a.probe -is [array] -and $a.probe.Count -gt 0) "$name must define probe array"
            Assert-True ($null -ne $a.probeModel -and $a.probeModel.Trim().Length -gt 0) "$name must define probeModel"
            Assert-True ($a.backgroundMode -in @("native", "runner-managed")) "$name backgroundMode must be native or runner-managed"
            Assert-True ($a.ContainsKey("experimental")) "$name must define experimental flag"
        }
    }

    It "carries --model into resume for every adapter whose start names one, or says in the registry why not" {
        # Measured 2026-09-12 (downstream project A, the P8 lane): agy's resume vector
        # had --model in `start` and not in `resume`, so every resumed turn ran
        # agy's GLOBAL DEFAULT model - the log says model="" and then
        # "Propagating selected model override to backend: label=Gemini 3.8 Flash
        # (High)" - while the runner's own lane log still printed the configured
        # model. A silent model swap on turn 2 of a night is invisible in every
        # place an operator looks, so it is pinned here rather than in prose.
        foreach ($name in (Get-AdapterNames -IncludeExperimental)) {
            $a = Get-AdapterTemplate $name -IncludeExperimental
            if (-not ($a.start -contains "--model")) { continue }
            if (-not ($a.resume -is [array]) -or -not $a.resume.Count) { continue }
            $hasModel = ($a.resume -contains "--model")
            $declared = ($a.ContainsKey("resumeModelNote") -and [string]$a["resumeModelNote"])
            Assert-True ($hasModel -or $declared) "$name has --model in start but not in resume: add it, or set resumeModelNote saying why the CLI does not need it"
            if ($hasModel) {
                Assert-True ($a.resume -contains "{{model}}") "$name resume names --model but never substitutes {{model}}"
            }
        }
    }

    Write-Host "`nAdapter-Specific Flags & Contracts"
    It "claude adapter specifies native daemon mode and valid attach tokens" {
        $claude = Get-AdapterTemplate "claude"
        Assert-Equal "native" $claude.backgroundMode
        Assert-True ($claude.start -contains "--bg") "claude start must include --bg"
        Assert-True ($claude.start -contains "--remote-control") "claude start must include --remote-control"
        Assert-True ($claude.list -contains "--json") "claude list must be json formatted"
        Assert-Equal "--allowedTools" $claude.allowedToolsFlag
        Assert-Equal "--permission-mode" $claude.permissionModeFlag
        Assert-True ($claude.idPattern.Length -gt 0) "claude must have idPattern"
    }

    It "agy adapter handles headless print mode, timeout override, and conversation resume" {
        $agy = Get-AdapterTemplate "agy"
        Assert-Equal "runner-managed" $agy.backgroundMode
        Assert-True ($null -eq $agy.list) "agy has no native list subcommand"
        Assert-True ($null -eq $agy.stop) "agy has no native stop subcommand"
        Assert-True ($agy.start -contains "-p") "agy must use -p for headless execution"
        Assert-True ($agy.start -contains "--print-timeout") "agy must pass --print-timeout"
        Assert-True ($agy.start -contains "--dangerously-skip-permissions") "agy must bypass permission prompts"
        Assert-True ($agy.resume -contains "--conversation") "agy resume must use --conversation, not attach"
        Assert-True ($agy.resume -contains "--model") "agy resume must pin the model; without it the resumed turn runs agy's global default"
        Assert-Equal "gemini-3.8-flash-high" $agy.probeModel
    }

    It "grok adapter configures workspace sandbox, --no-subagents, and --no-auto-update" {
        $grok = Get-AdapterTemplate "grok" -IncludeExperimental
        Assert-Equal "runner-managed" $grok.backgroundMode
        Assert-True ($grok.start -contains "--no-subagents") "grok must enforce leaf rule via --no-subagents"
        Assert-True ($grok.start -contains "--no-auto-update") "grok must suppress auto-update checks in headless mode"
        Assert-True ($grok.start -contains "--sandbox") "grok must enable sandboxing"
        Assert-True ($grok.start -contains "workspace") "grok sandbox must be workspace scoped"
        Assert-True ($grok.resume -contains "--resume") "grok resume must use --resume"
        Assert-Equal "grok-code" $grok.probeModel
    }

    It "codewhale adapter configures non-interactive exec and auto mode" {
        $cw = Get-AdapterTemplate "codewhale" -IncludeExperimental
        Assert-Equal "runner-managed" $cw.backgroundMode
        Assert-True ($cw.start -contains "exec") "codewhale must invoke exec subcommand"
        Assert-True ($cw.start -contains "--auto") "codewhale must pass --auto for autonomous loop"
        Assert-True ($cw.resume -contains "--resume") "codewhale resume must use --resume"
        Assert-Equal "deepseek-v4" $cw.probeModel
    }

    It "has no agent-canvas adapter: agent-canvas is a web-UI launcher, not a headless CLI" {
        # Measured 2026-09-18 (agent-canvas 1.19.0 --help): its only options are
        # --port, --public, --frontend-only, --backend-only, --info, --version.
        # An adapter driving --prompt/--model/--headless/--session cannot work,
        # and aliasing "openhands" to it would route work into a web server.
        Assert-True (-not ((Get-AdapterNames -IncludeExperimental) -contains "agent-canvas")) "agent-canvas must not be registered"
        Assert-Equal "openhands" (Resolve-AdapterName "openhands")
        Assert-Equal "canvas" (Resolve-AdapterName "canvas")
    }

    Write-Host "`nBuild-HandoffLaunchArgs with Adapters"
    It "renders claude launch args with tool allowlist and trailing prompt" {
        $cfg = @{
            launcher       = Get-AdapterTemplate "claude"
            allowedTools   = @("Bash", "Edit")
            permissionMode = "auto"
        }
        $vars = @{ rcName = "handoff-S1"; model = "opus"; prompt = "Write code with braces {test}" }
        $argv = Build-HandoffLaunchArgs $cfg "start" $vars
        Assert-Equal "--bg" $argv[0]
        Assert-Equal "--remote-control" $argv[1]
        Assert-Equal "handoff-S1" $argv[2]
        Assert-Equal "--allowedTools" $argv[3]
        Assert-Equal "Bash" $argv[4]
        Assert-Equal "Edit" $argv[5]
        Assert-Equal "--permission-mode" $argv[6]
        Assert-Equal "auto" $argv[7]
        Assert-Equal "--model" $argv[8]
        Assert-Equal "opus" $argv[9]
        Assert-Equal "Write code with braces {test}" $argv[10] "prompt must remain raw and atomic"
    }

    It "renders agy launch args without tools flag and with conversation resume" {
        $cfg = @{
            launcher = Get-AdapterTemplate "agy"
        }
        $vars = @{ model = "gemini-2.5-pro"; prompt = "Autonomous task" }
        $argv = Build-HandoffLaunchArgs $cfg "start" $vars
        Assert-Equal "-p" $argv[0]
        Assert-Equal "Autonomous task" $argv[1]
        Assert-Equal "--model" $argv[2]
        Assert-Equal "gemini-2.5-pro" $argv[3]
        Assert-True ($argv -contains "--dangerously-skip-permissions")
        Assert-True ($argv -contains "--print-timeout")

        $resumeVars = @{ sessionId = "conv-12345"; prompt = "Continue turn" }
        $resumeArgv = Build-HandoffLaunchArgs $cfg "resume" $resumeVars
        Assert-Equal "--conversation" $resumeArgv[0]
        Assert-Equal "conv-12345" $resumeArgv[1]
        Assert-Equal "-p" $resumeArgv[2]
        Assert-Equal "Continue turn" $resumeArgv[3]
    }

    Write-Host "`nConfig Integration & Launcher Shorthand"
    It "Read-HandoffConfig resolves launcher string shorthand to full adapter" {
        $baseCfg = @{
            repo     = $repoRoot
            sessions = @{ A = @{ name = "alpha"; model = "gemini-2.5-pro"; brief = "work" } }
            launcher = "agy"
        }
        $cfgPath = Join-Path $tmp "agy-config.json"
        $baseCfg | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding utf8
        $loaded = Read-HandoffConfig $cfgPath
        Assert-Equal "agy" $loaded.launcher.command
        Assert-Equal "runner-managed" $loaded.launcher.backgroundMode
        Assert-Equal "gemini-3.8-flash-high" $loaded.launcher.probeModel
    }

    It "Read-HandoffConfig allows overriding adapter properties via hashtable with adapter property" {
        $baseCfg = @{
            repo     = $repoRoot
            sessions = @{ A = @{ name = "alpha"; model = "grok-code"; brief = "work" } }
            launcher = @{ adapter = "grok"; probeModel = "grok-custom-probe" }
        }
        $cfgPath = Join-Path $tmp "grok-override.json"
        $baseCfg | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding utf8
        $loaded = Read-HandoffConfig $cfgPath
        Assert-Equal "grok" $loaded.launcher.command
        Assert-Equal "grok-custom-probe" $loaded.launcher.probeModel
        Assert-Equal "runner-managed" $loaded.launcher.backgroundMode
    }

    It "a session's launcher produces that agent's argument vector, not the default's" {
        $baseCfg = @{
            repo     = $repoRoot
            launcher = "claude"
            sessions = @{
                J = @{ name = "judge"; model = "opus"; brief = "judgement work" }
                M = @{ name = "mech";  model = "claude-sonnet-4-6"; brief = "mechanical work"
                       launcher = "agy" }
            }
        }
        $cfgPath = Join-Path $tmp "per-session-launcher.json"
        $baseCfg | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding utf8
        $loaded = Read-HandoffConfig $cfgPath
        Assert-Equal "claude" $loaded.launcher.command "the default must stay claude"

        $jc = $loaded.Clone(); $jc["launcher"] = Resolve-HandoffSessionLauncher $loaded "J" $loaded.launcher
        $jargv = Build-HandoffLaunchArgs $jc "start" @{ rcName = "handoff-J"; model = "opus"; prompt = "go" }
        Assert-Equal "--bg" $jargv[0]

        $mc = $loaded.Clone(); $mc["launcher"] = Resolve-HandoffSessionLauncher $loaded "M" $loaded.launcher
        $margv = Build-HandoffLaunchArgs $mc "start" @{ model = "claude-sonnet-4-6"; prompt = "go" }
        Assert-Equal "-p" $margv[0]
        Assert-Equal "go" $margv[1]
        Assert-True ($margv -contains "--dangerously-skip-permissions") "the agy vector must be used"
        Assert-True (-not ($margv -contains "--bg")) "claude's flags must not reach an agy session"
    }

    Write-Host "`nSubagent Hierarchy & Depth Policy"
    It "Build-HandoffSubagentPolicy enforces 3-level maxDepth by default" {
        $cfg = @{
            subagents = @{
                maxConcurrent = 2
                tiers         = @{ mechanical = "sonnet"; judgement = "opus" }
                leafRule      = $true
            }
        }
        $policy = Build-HandoffSubagentPolicy $cfg
        Assert-Match $policy "Delegation depth is capped at 3 levels"
        Assert-Match $policy "Main orchestrator -> phase-specific orchestrator subagent -> task-specific subagent orchestrator"
        Assert-Match $policy "Subagents at level 3 are leaves: they do NOT spawn further subagents"
    }

    It "Build-HandoffSubagentPolicy respects custom maxDepth setting" {
        $cfg = @{
            subagents = @{
                maxConcurrent = 4
                maxDepth      = 2
                tiers         = @{ mechanical = "flash"; judgement = "pro" }
                leafRule      = $true
            }
        }
        $policy = Build-HandoffSubagentPolicy $cfg
        Assert-Match $policy "Delegation depth is capped at 2 levels"
        Assert-Match $policy "Subagents at level 2 are leaves"
    }

    Write-Host "`nPrompt Integration & Single-Source Sync"
    It "New-HandoffConfigScaffold inlines prompts/unattended-orchestration.md when present" {
        $scaffoldPath = Join-Path $tmp "scaffold-test.json"
        New-HandoffConfigScaffold -Path $scaffoldPath -RepoRoot $skillRepoRoot -Force | Out-Null
        $content = Get-Content $scaffoldPath -Raw
        Assert-Match $content '"launcher":\s*"claude"' "scaffold sets default launcher"
        Assert-Match $content '"maxDepth":\s*3' "scaffold sets 3-level subagent depth"
        Assert-Match $content "general purpose of this project" "controllerBrief must inline prompt template"
        Assert-Match $content "plots for visualization" "controllerBrief must retain research grade reporting criteria"
        Assert-Match $content "cross memory should be done via omnigraph" "controllerBrief must retain MCP memory instructions"
        Assert-Match $content "\{\{sessionsTable\}\}" "controllerBrief must append sessions table placeholder"
    }

    It "New-HandoffConfigScaffold falls back to a generic controller brief with no prompt file" {
        # The other half of the same contract, and the reason the test above can be trusted: an
        # adopting repository has no prompts/ directory and -Init must still produce a usable
        # config there. Without this case, a scaffold that ignored -RepoRoot entirely and always
        # inlined this repo's prompt would pass the suite.
        $bare = Join-Path $tmp "bare-repo"
        New-Item -ItemType Directory -Force -Path $bare | Out-Null
        $scaffoldPath = Join-Path $tmp "scaffold-bare.json"
        # Run from a directory inside no git repo, so the scaffold's cwd fallback cannot reach this
        # skill's own prompts/ file and answer on the bare root's behalf.
        Push-Location $tmp
        try { New-HandoffConfigScaffold -Path $scaffoldPath -RepoRoot $bare -Force | Out-Null }
        finally { Pop-Location }
        $content = Get-Content $scaffoldPath -Raw
        Assert-Match $content "Orchestrate the following sessions" "a bare repo gets the generic brief"
        Assert-NotMatch $content "general purpose of this project" "there is nothing to inline"
        Assert-Match $content "\{\{sessionsTable\}\}" "the placeholder is appended either way"
    }

    Write-Host "`nDiscovery & Installation Heuristics"
    It "Test-AdapterInstalled detects claude when present on system" {
        $hasClaude = Test-AdapterInstalled "claude"
        # Claude is installed on this environment
        Assert-True $hasClaude "claude should be detected on this host"
    }

    It "Get-AdapterInstallCommand returns valid on-demand commands" {
        $cmdAgy = Get-AdapterInstallCommand "agy"
        Assert-Match $cmdAgy "https://antigravity.google/cli/install"

        $cmdClaude = Get-AdapterInstallCommand "claude"
        Assert-Match $cmdClaude "npm install -g @anthropic-ai/claude-code"

        $cmdGrok = Get-AdapterInstallCommand "grok"
        Assert-Match $cmdGrok "npm install -g @xai-official/grok"

        $cmdCw = Get-AdapterInstallCommand "codewhale"
        Assert-Match $cmdCw "npm install -g codewhale"
    }
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "`n==============================================="
Write-Host "Adapter Contract Suite Results: $script:Ran ran, $script:Failures failed."
if ($script:Failures -gt 0) { exit 1 } else { exit 0 }
