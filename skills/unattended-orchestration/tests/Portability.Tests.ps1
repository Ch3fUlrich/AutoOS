<#
Adoption test — the one that matters for reuse.

Copies the skill folder into a FRESH git repository, in a directory that has
nothing to do with this one, and drives the whole entry path there **without
editing a single file**:

    -Init  ->  -Validate  ->  -EmitBriefs  ->  -DryRun

If this suite passes, "copy the skill into your repo and go" is a true statement.
If it fails, the skill has re-acquired a dependency on this repository.

    pwsh -NoProfile -File skills/unattended-orchestration/tests/Portability.Tests.ps1

Nothing here launches a session: -DryRun stops before any launch.
#>
$ErrorActionPreference = "Stop"
$script:Failures = 0
$script:Ran = 0

function It([string]$name, [scriptblock]$body) {
    $script:Ran++
    try { & $body; Write-Host "  ok   $name" -ForegroundColor Green }
    catch { $script:Failures++; Write-Host "  FAIL $name`n       $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-Match([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -notmatch $pattern) { throw "expected /$pattern/ $because`n--- got ---`n$text" }
}
function Assert-NotMatch([string]$text, [string]$pattern, [string]$because = "") {
    if ($text -match $pattern) { throw "did NOT expect /$pattern/ $because`n--- got ---`n$text" }
}

$skillDir = Split-Path $PSScriptRoot -Parent
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("handoff-adopt-" + [guid]::NewGuid().ToString("N"))
$fakeRepo = Join-Path $sandbox "some-other-project"

try {
    # A brand-new repository that has never heard of agent-skills.
    New-Item -ItemType Directory -Force -Path $fakeRepo | Out-Null
    Push-Location $fakeRepo
    try {
        & git init -q 2>&1 | Out-Null
        & git config user.email "adopt@test.local" 2>&1 | Out-Null
        & git config user.name "Adoption Test" 2>&1 | Out-Null
        & git symbolic-ref HEAD refs/heads/trunk 2>&1 | Out-Null   # deliberately NOT "main"
        "hello" | Set-Content (Join-Path $fakeRepo "README.md")
        & git add -A 2>&1 | Out-Null
        & git commit -qm "initial" 2>&1 | Out-Null
    }
    finally { Pop-Location }

    # Copy the skill in, exactly as a human or agent would.
    $dest = Join-Path $fakeRepo "skills/unattended-orchestration"
    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    Copy-Item -Recurse -Force $skillDir $dest
    $runner = Join-Path $dest "run_handoff_sessions.ps1"

    Write-Host "`nAdoption — copy the folder in, run it unedited"

    It "the copied skill carries no path belonging to the source repository" {
        $texts = Get-ChildItem $dest -Recurse -File -Include *.ps1, *.psm1, *.json |
            Where-Object { $_.FullName -notmatch '\\tests\\' } |
            ForEach-Object { Get-Content $_.FullName -Raw }
        $joined = $texts -join "`n"
        Assert-NotMatch $joined "agent-skills" "the skill must not reference the repo it came from"
        # Any real user home, raw or JSON-escaped; placeholders such as C:\Users\<you> pass.
        Assert-NotMatch $joined '[A-Za-z]:(\\\\|\\)Users(\\\\|\\)(?!(?:Public|you)\b)\w[\w.-]*' "no absolute developer path may survive the copy"
    }

    It "-Init scaffolds a config and detects the repo's real branch (trunk, not main)" {
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -Init 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "wrote" "should report what it wrote`n$out"
        Assert-Match $out "trunk" "must detect the actual branch, not assume main"
        if (-not (Test-Path (Join-Path $fakeRepo ".claude/handoff.config.json"))) { throw "no config was written" }
    }

    It "the scaffolded config contains no absolute path, so it is committable" {
        $text = Get-Content (Join-Path $fakeRepo ".claude/handoff.config.json") -Raw
        Assert-NotMatch $text ([regex]::Escape($fakeRepo)) "the scaffold must rely on repo auto-detection"
        Assert-NotMatch $text "C:\\\\Users" "no machine-specific path in a committed config"
    }

    It "-Init refuses to clobber an existing config" {
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -Init 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "already exists|exists" "a second -Init must refuse`n$out"
    }

    It "-Validate accepts the scaffold with NO editing at all" {
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -Validate 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "config OK"
        Assert-Match $out "base:\s+trunk" "must adopt the detected branch"
        Assert-Match $out ([regex]::Escape("some-other-project")) "must resolve to the adopting repo"
    }

    It "finds its config from a SUBDIRECTORY of the adopting repo" {
        $sub = Join-Path $fakeRepo "src/deep"
        New-Item -ItemType Directory -Force -Path $sub | Out-Null
        Push-Location $sub
        try { $out = (& pwsh -NoProfile -File $runner -Validate 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "config OK" "running from a subdirectory must still work`n$out"
    }

    It "-EmitBriefs renders the scaffolded session with every placeholder resolved" {
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -EmitBriefs 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "### Session example"
        Assert-Match $out ([regex]::Escape("some-other-project-example")) "worktree must derive from the adopting repo's name"
        if ($out -match '\{\{\w+\}\}') { throw "unexpanded placeholder in an emitted brief:`n$out" }
    }

    It "-DryRun completes in the adopting repo without touching this one" {
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -DryRun 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "dry run: would create"
        Assert-NotMatch $out "agent-skills" "the run must not reach back into the source repository"
    }

    It "writes its state inside the adopting repo, not the source" {
        if (-not (Test-Path (Join-Path $fakeRepo "output/sessions"))) { throw "state dir not created in the adopting repo" }
    }

    Write-Host "`nAdoption — swapping the agent"
    It "a config naming a different launcher is accepted and reported" {
        $cfgPath = Join-Path $fakeRepo ".claude/handoff.config.json"
        $c = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $c | Add-Member -NotePropertyName launcher -NotePropertyValue ([pscustomobject]@{ command = "my-agent" }) -Force
        $c | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding utf8
        Push-Location $fakeRepo
        try { $out = (& pwsh -NoProfile -File $runner -Validate 2>&1 | Out-String) } finally { Pop-Location }
        Assert-Match $out "my-agent" "the configured launcher must be surfaced`n$out"
    }
}
finally {
    Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
}

Write-Host "`n$($script:Ran) assertions, $($script:Failures) failed."
exit $script:Failures
