<#
    McpTopology.Tests.ps1 — Contract tests for MCP topology, worktree trust, and tool-layer leaf enforcement
#>

$ErrorActionPreference = "Stop"
$script:Ran = 0; $script:Failures = 0

function It([string]$Name, [scriptblock]$Body) {
    $script:Ran++
    try {
        & $Body
        Write-Host "  ok   $Name" -ForegroundColor Green
    } catch {
        $script:Failures++
        Write-Host "  FAIL $Name`n       $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal($Expected, $Actual, [string]$Message = "") {
    if ($Expected -ne $Actual) {
        throw "assert equal failed $(if ($Message) { "($Message): " })expected '$Expected', got '$Actual'"
    }
}

function Assert-True([bool]$Condition, [string]$Message = "expected true") {
    if (-not $Condition) { throw "assert true failed: $Message" }
}

function Assert-Match([string]$Value, [string]$Pattern, [string]$Message = "") {
    if ($Value -notmatch $Pattern) {
        throw "assert match failed $(if ($Message) { "($Message): " })'$Value' does not match pattern '$Pattern'"
    }
}

$modulePath = Join-Path $PSScriptRoot "..\HandoffCore.psm1"
$adaptersPath = Join-Path $PSScriptRoot "..\AgentAdapters.psm1"
$trustScript = Join-Path $PSScriptRoot "..\trust_worktree.py"
Import-Module (Resolve-Path $modulePath).Path -Force
Import-Module (Resolve-Path $adaptersPath).Path -Force

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "mcp-topology-test-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    Write-Host "`nMCP Policy Construction (Build-HandoffMcpPolicy)"

    $fakeRepo = Join-Path $tmp "my-cool-project"
    New-Item -ItemType Directory -Force -Path $fakeRepo | Out-Null
    $cfg = @{
        repo = $fakeRepo
        worktreePrefix = "my-cool-project"
        worktreeParent = $tmp
        sessions = @{
            worker = @{ name = "worker"; model = "sonnet"; brief = "implement feature" }
        }
    }
    $vars = Get-HandoffSessionVars $cfg "worker"

    It "Build-HandoffMcpPolicy references the router skill" {
        $policy = Build-HandoffMcpPolicy $cfg $vars
        Assert-Match $policy "skills/repository-index/SKILL\.md" "must reference router"
    }

    It "Build-HandoffMcpPolicy mandates Serena with absolute worktree path" {
        $policy = Build-HandoffMcpPolicy $cfg $vars
        Assert-Match $policy "activate_project\('$([regex]::Escape($vars.worktree))'\)" "must activate Serena with absolute path"
        Assert-Match $policy "One active project per session" "must enforce single active project"
    }

    It "Build-HandoffMcpPolicy pins Omnigraph to repo folder name" {
        $policy = Build-HandoffMcpPolicy $cfg $vars
        Assert-Match $policy "OMNIGRAPH_GRAPH_ID='my-cool-project'" "must pin to repository name"
        Assert-Match $policy "Never write project data to the global 'memory' graph" "must forbid writing to memory graph"
    }

    It "Build-HandoffMcpPolicy points to linked graphify-out directory" {
        $policy = Build-HandoffMcpPolicy $cfg $vars
        Assert-Match $policy "graphify-out" "must reference graphify-out"
    }

    It "Get-HandoffSessionVars exposes mcpPolicy without unresolved placeholders" {
        Assert-True ($vars.ContainsKey("mcpPolicy")) "vars must contain mcpPolicy"
        Assert-True ($vars["mcpPolicy"].Length -gt 0) "mcpPolicy must not be empty"
        if ($vars["mcpPolicy"] -match '\{\{\w+\}\}') {
            throw "unresolved template token in mcpPolicy: $($vars['mcpPolicy'])"
        }
    }

    Write-Host "`nTool-Layer Leaf Enforcement"

    It "Claude adapter specifies leafEnforcement flag" {
        $claude = Get-AdapterTemplate "claude"
        Assert-Equal "--disallowedTools Agent" $claude["leafEnforcement"] "Claude leaf enforcement flag"
    }

    It "Grok adapter specifies leafEnforcement flag" {
        $grok = Get-AdapterTemplate "grok" -IncludeExperimental
        Assert-Equal "--no-subagents" $grok["leafEnforcement"] "Grok leaf enforcement flag"
    }

    It "Build-HandoffSubagentPolicy includes CLI leaf enforcement instructions" {
        $cfgClaude = @{
            launcher = @{ command = "claude" }
            subagents = @{ maxConcurrent = 2; maxDepth = 3; leafRule = $true }
        }
        $policyClaude = Build-HandoffSubagentPolicy $cfgClaude
        Assert-Match $policyClaude "Delegation depth is capped at 3 levels" "depth cap"
        Assert-Match $policyClaude "--disallowedTools Agent" "must include Claude leaf flag"

        $cfgGrok = @{
            launcher = @{ command = "grok" }
            subagents = @{ maxConcurrent = 2; maxDepth = 3; leafRule = $true }
        }
        $policyGrok = Build-HandoffSubagentPolicy $cfgGrok
        Assert-Match $policyGrok "--no-subagents" "must include Grok leaf flag"
    }

    It "Build-HandoffLaunchArgs filters Agent tool and appends leaf flag when isLeaf is true" {
        $fullCfg = @{
            launcher = (Get-AdapterTemplate "claude")
            allowedTools = @("Read", "Write", "Edit", "Bash", "Agent")
            permissionMode = "auto"
        }
        # Normal orchestrator launch
        $normalVars = @{ rcName = "rc1"; model = "sonnet"; prompt = "task"; isLeaf = $false }
        $normalArgs = Build-HandoffLaunchArgs $fullCfg "start" $normalVars
        Assert-True (@($normalArgs) -contains "Agent") "normal session has Agent tool"
        Assert-True (@($normalArgs) -notcontains "--disallowedTools") "normal session does not disallow tools"

        # Leaf subagent launch
        $leafVars = @{ rcName = "rc1"; model = "sonnet"; prompt = "task"; isLeaf = $true }
        $leafArgs = Build-HandoffLaunchArgs $fullCfg "start" $leafVars
        $allowedIdx = [array]::IndexOf($leafArgs, "--allowedTools")
        $permIdx = [array]::IndexOf($leafArgs, "--permission-mode")
        $disallowedIdx = [array]::IndexOf($leafArgs, "--disallowedTools")

        $allowedSection = $leafArgs[($allowedIdx + 1)..($permIdx - 1)]
        Assert-True (@($allowedSection) -notcontains "Agent") "leaf session strips Agent from allowedTools"
        Assert-True ($disallowedIdx -ge 0) "leaf session adds --disallowedTools"
        Assert-Equal "Agent" $leafArgs[$disallowedIdx + 1] "leaf session specifies Agent as disallowed"
    }

    Write-Host "`nScaffold Defaults with MCP Topology"

    It "New-HandoffConfigScaffold declares linkDirs graphify-out and copyFiles .env" {
        $scaffoldPath = Join-Path $tmp "scaffold-mcp.json"
        New-HandoffConfigScaffold -Path $scaffoldPath -RepoRoot $fakeRepo -BaseBranch "main" | Out-Null
        $scaffold = Get-Content $scaffoldPath -Raw | ConvertFrom-Json
        Assert-True (@($scaffold.linkDirs) -contains "graphify-out") "scaffold must declare linkDirs graphify-out"
        Assert-True (@($scaffold.copyFiles) -contains ".env") "scaffold must declare copyFiles .env"
        Assert-Match $scaffold.briefTemplate "\{\{mcpPolicy\}\}" "scaffold briefTemplate must include {{mcpPolicy}}"
    }

    Write-Host "`nWorktree Trust & Omnigraph Setup (trust_worktree.py)"

    It "trust_worktree.py injects OMNIGRAPH_GRAPH_ID into worktree .env" {
        $worktreeDir = Join-Path $tmp "wt-trust"
        New-Item -ItemType Directory -Force -Path $worktreeDir | Out-Null
        
        # Run python script with --repo and worktree
        $res = & python $trustScript $worktreeDir --repo $fakeRepo 2>&1 | Out-String
        Assert-True ($LASTEXITCODE -eq 0) "trust_worktree.py must succeed`n$res"

        $envPath = Join-Path $worktreeDir ".env"
        Assert-True (Test-Path $envPath) "must create .env in worktree"
        $envContent = Get-Content $envPath -Raw
        Assert-Match $envContent "OMNIGRAPH_GRAPH_ID=my-cool-project" "must set OMNIGRAPH_GRAPH_ID to repo name"
    }

    It "trust_worktree.py accepts --agent and supports --check" {
        $worktreeDir = Join-Path $tmp "wt-trust"
        $checkRes = & python $trustScript $worktreeDir --repo $fakeRepo --agent agy --check 2>&1 | Out-String
        Assert-True ($LASTEXITCODE -eq 0) "trust_worktree.py --check must succeed`n$checkRes"
        Assert-Match $checkRes "WOULD" "check mode must report prospective actions"
    }

} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "`n$($script:Ran) assertions, $($script:Failures) failed."
exit $script:Failures
