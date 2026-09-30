# Lane AdmissionFix — apply.ps1 shim fix + launcher admission/shim test (ws-admission-fix-20260930)

Branch: `L1-backlog/ws-admission-fix-20260930`
Worktree: `AutoOS-ws-admission-fix` (created with `git worktree add ... origin/main`).
Base sha: `6ec0605a188586778acac6829a9263181129e949` — the `origin/main` tip at worktree
creation. `origin/main` advanced to `e58274a` mid-lane; the branch point stays `6ec0605`.
No secrets below: env/key names and relative paths only (the one resolved shim path is
shown in its `%APPDATA%` env-var form, per the redaction rule).

## Findings (from L0)

1. `configuration/omniroute/apply.ps1` bare-shim bug — the same defect lane P0 fixed in
   `configuration/start-stack.ps1`: a bare `Start-Process -FilePath 'omniroute'` resolves
   the npm `.ps1` shim (an ExternalScript), which `Start-Process` cannot launch as a Win32
   app, so the gateway silently never starts. It must resolve `omniroute.cmd` explicitly
   (PATH lookup + `$env:APPDATA\npm` fallback, no hardcoded user path).
2. A launcher admission/shim test was missing.

## Commit

`880ec58` — `fix(admission): pin omniroute.cmd shim in gateway launchers + launcher admission/shim test`

Full sha: `880ec588eb786e4c8ae7a222446b07de4a850eeb`.
`git show --stat HEAD` (header + stat verbatim; the 3-paragraph message body elided):

```
commit 880ec588eb786e4c8ae7a222446b07de4a850eeb
Author: <author; identity redacted per repo rule 1>
Date:   Wed Sep 30 22:33:48 2026 +0200

    fix(admission): pin omniroute.cmd shim in gateway launchers + launcher admission/shim test
    <message body>

 configuration/omniroute/apply.ps1 | 23 ++++++++++++++++++++++-
 configuration/start-stack.ps1     | 23 ++++++++++++++++++++++-
 tests/run-tests.ps1               | 26 ++++++++++++++++++++++++++
 3 files changed, 70 insertions(+), 2 deletions(-)
```

## Diff summary

- `configuration/omniroute/apply.ps1` (gateway-spawn block, lines 88-110): now
  (a) defaults `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=8` **only when unset** (respect-set),
  and (b) resolves `omniroute.cmd` via `Get-Command` (PATH) with a
  `Join-Path $env:APPDATA 'npm\omniroute.cmd'` fallback, then a `Test-Path -LiteralPath`
  guard that prints a clear message and `exit 1` when it is missing, then
  `Start-Process -FilePath $omnirouteCmd ...`.
  StrictMode-safe: the resolution captures
  `$omnirouteCmdInfo = Get-Command omniroute.cmd ...` and reads `$omnirouteCmdInfo.Source`
  behind a null test — P0's one-liner `(Get-Command omniroute.cmd ...).Source` throws under
  `Set-StrictMode -Version Latest` when the shim is absent, and `apply.ps1` sets StrictMode.
- `configuration/start-stack.ps1` (lines 110-132): the same shim + admission-default block.
  P0's fix lives on `L1-backlog/ws-p0-admission-fix-20260930` (`d88ed3b`); this branch's base
  predates that branch, so the new test covers both launchers to stay self-consistent.
- `tests/run-tests.ps1` (line 9779): new `Test-Case` — see §2.

## Verification (verbatim)

### 1. Failing-first — the test against PRE-FIX code

Run in a throwaway detached worktree at `origin/main` (pre-fix launchers + the new test):

```
  X omniroute launchers pin the .cmd shim, fail loudly without it, and default chat admission without clobbering
      configuration\omniroute\apply.ps1 still starts the bare 'omniroute' name
      at Assert-True, <pre-fix-worktree>\tests\run-tests.ps1: line 91 ...
  passed 2   failed 1   skipped 0
EXIT=1
```

(The `passed 2` are the unrelated pre-existing "shim directories" tests that the
`-Filter shim` name match also selects.) The throwaway worktree was deleted and pruned.

### 2. The new test PASSES post-fix

`pwsh -NoProfile -File tests/run-tests.ps1 -Filter shim`:

```
  + omniroute launchers pin the .cmd shim, fail loudly without it, and default chat admission without clobbering
  ... (18 assertion lines, one per Assert)
  passed 20   failed 0   skipped 0
EXIT=0
```

Test body asserts, for each of `configuration\omniroute\apply.ps1` and
`configuration\start-stack.ps1`:
(a) no `Start-Process -FilePath 'omniroute'`; resolves `omniroute.cmd` via PATH; has the
`if (-not $omnirouteCmd) { $omnirouteCmd = Join-Path $env:APPDATA ... }` fallback; starts
`-FilePath $omnirouteCmd`; contains no `C:\Users\<name>` hardcoded path;
(b) a `Test-Path -LiteralPath $omnirouteCmd` guard whose next lines `exit 1`;
(c) an `if (-not $env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT)` guard and exactly ONE
`OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT = '8'` assignment (idempotent).

### 3. PowerShell parse errors = 0 (touched .ps1)

```
configuration/omniroute/apply.ps1 parse-errors=0
configuration/start-stack.ps1 parse-errors=0
tests/run-tests.ps1 parse-errors=0
```

### 4. `apply.ps1 -DryRun` → Gateway OK, no spawn

`pwsh -NoProfile -File configuration/omniroute/apply.ps1 -DryRun`:

```
This is a dry run - nothing is registered, created or started.
No configuration\api-keys.yml yet - copy configuration\api-keys.example.yml and fill it in.
Continuing with combos only.
Gateway OK on http://127.0.0.1:20128
...
Done. Apps pick this up on next start (configuration\start-stack.ps1).
EXIT=0
```

No gateway process was started or restarted (dry-run branch).

### 5. `Invoke-ScriptAnalyzer` — no NEW findings vs pre-fix

The repo's own static-analysis test only gates `lib\windows\*.psm1` + `setup.ps1`, not
`configuration\`. Re-run with the repo's own rule exclusions
(`PSUseShouldProcessForStateChangingFunctions`, `PSAvoidUsingWriteHost`, `PSUseSingularNouns`),
comparing a real detached `origin/main` worktree against the branch:

```
configuration/omniroute/apply.ps1  prefixFindings=1  (PSUseDeclaredVarsMoreThanAssignments @line 52)
configuration/start-stack.ps1      prefixFindings=0
run-tests.ps1 pre=17 post=17   no rule-set difference
```

The single `apply.ps1` finding (`KeysMissing` assigned but unused, line 52) is pre-existing
and unrelated to the changed block; the touch adds none.

### 6. Behavioral checks (beyond source assertions)

Extracting the real admission guard from `apply.ps1` and evaluating it:

```
preset=4        -> 4
unset          -> 8
second run     -> 8
```

`Get-Command omniroute.cmd` on this host resolves
`%APPDATA%\npm\omniroute.cmd` (expands to the same path on any host; username redacted).
Extracting the real shim-resolution block and running it in a child with an empty PATH and
a bogus APPDATA:

```
Could not find omniroute.cmd (the npm shim). Run: .\setup.ps1 -Only omniroute -Yes
MISS-EXIT=1
```

## Out of scope (observed, NOT changed — reported for L0)

- `configuration/autostart/Start-AutoOSStack.ps1:47` carries the SAME bare
  `Start-Process -FilePath 'omniroute'` pattern. It is not one of the two files the finding
  named and its failure branch continues (does not `exit`), so the fix shape differs; left
  untouched here.
- The bare-shim class also appears for the npm CLIs `opencode`
  (`start-stack.ps1:242`, `Start-AutoOSStack.ps1:110`) and `litellm`
  (`configuration/litellm/start-litellm.ps1:91`). Out of the omniroute finding's scope.

## Review (item 5) — reviewer → fixer → re-review

- Writer: this lane (`deepseek-v4.1-flash` via `omniroute`).
- Reviewer: `t3-reviewer` (session `ses_f0bf9ba21ffecmB0MYrGDZOmto`), configured model
  `omniroute/t3-driver` (`opencode.jsonc:102`) — a different model family from the writer
  (mistral/GLM-class legs), independent fresh context.
- What it read: `git show 880ec58`; `configuration/omniroute/apply.ps1` (shim + default +
  loud-failure block); `configuration/start-stack.ps1` (same block);
  `tests/run-tests.ps1` new `Test-Case`; and it ran
  `pwsh -NoProfile -File tests/run-tests.ps1 -Filter shim`.
- Verdict: **PASS** — "All required criteria are satisfied. The commit fully addresses both
  review findings." It independently confirmed no hardcoded user paths/secrets in the diff
  and that the admission var is set only when absent (no clobber).

---

# Follow-up: reviewer-B finding — the autostart site (ws-admission-fix-20260930)

Two independent admission reviewers read the lane. **Reviewer A** (`nemotron-3-ultra-free`)
returned **PASS**. **Reviewer B** (`longcat-2.5-preview-free`) returned **FAIL**: the same
bare-`omniroute` shim class still lived at `configuration/autostart/Start-AutoOSStack.ps1:47`,
with a different, non-`exit` failure shape. **The original handoff MISSED this site** — it was
recorded above only as an "out of scope" observation (§Out of scope), not fixed. Reviewer-B is
correct; the queue head is this follow-up.

## Commit

`1179e3f` — `fix(admission): pin omniroute.cmd shim in the logon resume helper (reviewer-B finding) + test`

Full sha: `1179e3f2cf973dcdf74b9daefc42ec4297a87e1c`. `git show --stat HEAD` (header + stat
verbatim; message body elided):

```
commit 1179e3f2cf973dcdf74b9daefc42ec4297a87e1c
Author: <author; identity redacted per repo rule 1>
Date:   Wed Sep 30 22:39:53 2026 +0200

    fix(admission): pin omniroute.cmd shim in the logon resume helper (reviewer-B finding) + test
    <message body>

 configuration/autostart/Start-AutoOSStack.ps1 | 25 ++++++++++++++++++++++---
 tests/run-tests.ps1                           | 10 +++++++---
 2 files changed, 29 insertions(+), 6 deletions(-)
```

## Diff summary

- `configuration/autostart/Start-AutoOSStack.ps1` (gateway block, lines 40-69): the
  `elseif (-not (Get-Command omniroute ...))` fall-through branch is gone. The
  gateway-down branch now (a) defaults `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT=8` **only when
  unset** (respect-set), then (b) resolves `omniroute.cmd` via `Get-Command` (PATH) with a
  `Join-Path $env:APPDATA 'npm\omniroute.cmd'` fallback, guarded by
  `Test-Path -LiteralPath $omnirouteCmd`, which prints a clear message and `exit 1` when
  missing, then (c) `Start-Process -FilePath $omnirouteCmd ...`. Byte-for-byte the same block
  `880ec58` put in `apply.ps1`/`start-stack.ps1`. **Behaviour change:** a missing shim now
  aborts the resume (`exit 1`) instead of logging and continuing — the loud failure the
  reviewer asked for; because the gateway is what litellm/openhands/opencode route through,
  a broken gateway is a broken stack, and a non-zero exit is the signal a logon task's log
  does not give.
- `tests/run-tests.ps1` (line 9779 Test-Case): the assertion loop now also covers
  `configuration\autostart\Start-AutoOSStack.ps1` — same three checks (.cmd resolution,
  missing-shim `exit 1`, admission default exactly once). One loop, three files: no duplicated
  test body.

## Verification (verbatim)

### 1. Failing-first — the extended test against PRE-FIX code

`pwsh -NoProfile -File tests/run-tests.ps1 -Filter shim` before applying the source fix:

```
  X omniroute launchers pin the .cmd shim, fail loudly without it, and default chat admission without clobbering
      configuration\autostart\Start-AutoOSStack.ps1 still starts the bare 'omniroute' name
      at Assert-True, <worktree>\tests\run-tests.ps1: line 91 | at <ScriptBlock>, ... line 9794
  passed 20   failed 1   skipped 0
EXIT=1
```

### 2. The extended test PASSES post-fix

`pwsh -NoProfile -File tests/run-tests.ps1 -Filter shim`:

```
  + omniroute launchers pin the .cmd shim, fail loudly without it, and default chat admission without clobbering
  ... (27 assertion lines: 9 per launcher x 3 launchers; + 2 unrelated pre-existing shim tests)
  passed 29   failed 0   skipped 0
EXIT=0
```

### 3. PowerShell parse errors = 0 (touched .ps1)

```
configuration\autostart\Start-AutoOSStack.ps1 parse-errors=0
tests\run-tests.ps1 parse-errors=0
```

### 4. `-DryRun` — not applicable; behavioural substitute

`Start-AutoOSStack.ps1` takes no parameters and has no dry-run branch (it is the no-arg logon
resume helper), so there is no `-DryRun` to run here. Instead the real shim-resolution block
was extracted from the file and evaluated in a child `pwsh` with an empty `PATH` and a bogus
`APPDATA` (so neither PATH nor the fallback can resolve):

```
Could not find omniroute.cmd (the npm shim). Run: .\setup.ps1 -Only omniroute -Yes
MISS-EXIT=1
```

(The first attempt fed the block through `& script.ps1`, which only sets `$LASTEXITCODE` for
that script scope and let the child exit 0 with a stray `NO-EXIT` line; rerunning with
`-File` propagates the `exit 1` — `MISS-EXIT=1` above. The wrong run is recorded so the
correction is visible, not hidden.)

### 5. `Invoke-ScriptAnalyzer` — no NEW findings vs pre-fix

Same command both sides (repo gating severity + repo exclusions):
`Invoke-ScriptAnalyzer -Severity Error,Warning -ExcludeRule PSUseShouldProcessForStateChangingFunctions,PSAvoidUsingWriteHost,PSUseSingularNouns`.
Pre-fix copies were taken raw via `cmd /c "git show HEAD:<path> > <tmp>"` (a PowerShell
pipeline mangles the UTF-8 BOM into parse errors — the first attempt reported a bogus
`pre=24`; corrected below):

```
configuration\autostart\Start-AutoOSStack.ps1  pre=0  post=0
tests\run-tests.ps1                            pre=17  post=17
```

No new finding. The 17 `run-tests.ps1` findings are the same pre-existing ones (approved-verb
`Describe-Group`, positional `Assert-Equal`, etc.) — only line numbers moved.

### 6. Behavioural admission guard

Same respect-set shape as `apply.ps1`: `preset=4 -> 4`, `unset -> 8`, `second run -> 8`
(no duplicate assignment; a second run is idempotent).

## Worktree / cleanliness

`git status --short --branch` (after the commit) → branch
`L1-backlog/ws-admission-fix-20260930` with **no tracked modifications** (the ignored `logs/`
DONE note is added with `git add -f`). No gateway restart; `chat_admission_busy` /
`Rate limit exceeded` observed: 0; backoffs: 0; refusals: none.

## Both reviewers, verbatim findings and verdicts

- Reviewer **A** — `nemotron-3-ultra-free` — **PASS** on `880ec58`: no hardcoded user
  paths/secrets, admission var set only when absent.
- Reviewer **B** — `longcat-2.5-preview-free` — **FAIL**, finding: the autostart site
  (`configuration/autostart/Start-AutoOSStack.ps1:47`) still uses the bare-`omniroute` shim
  with a non-`exit` failure shape; it must resolve `omniroute.cmd` and fail loudly. This
  commit closes exactly that finding. **The site was MISSED by the original handoff.**

## Re-review (different family, item 7)

- Reviewer: `t3-reviewer` subagent, session `ses_f0bf43ef0ffeJm4L8G200OpNw9`, configured
  model `omniroute/t3-driver` (`opencode.jsonc`) — a different model family from the writer
  (`deepseek-v4.1-flash`), independent fresh context.
- What it read: `git show 1179e3f` (stat + diff), `configuration/autostart/Start-AutoOSStack.ps1`
  in full, and the `Test-Case` at `tests/run-tests.ps1:9779`; it ran
  `pwsh -NoProfile -File tests/run-tests.ps1 -Filter shim` itself.
- Verdict: **PASS** — it checked each of: `.cmd` resolution via `Get-Command` + `%APPDATA%\npm`
  fallback and `-FilePath $omnirouteCmd`; the missing-shim message + `exit 1`; the
  `if (-not $env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT) { ... = '8' }` respect-set-and-once shape;
  no hardcoded user path / secret; the autostart file in the test loop with the focused run
  green; and parse errors = 0. No defect found ("VERDICT: PASS").

## Recorded, NOT fixed (item 6) — same bare-shim class for the node CLIs

Surveyed with `Select-String -Pattern "Start-Process\s+-FilePath\s+'(omniroute|opencode|litellm|node|npm|claude)'"`;
left untouched, reported for L0:

- `configuration/autostart/Start-AutoOSStack.ps1:129` — `Start-Process -FilePath 'opencode' ...`
  (opencode is an npm CLI: the same `.ps1`-ExternalScript class).
- `configuration/start-stack.ps1:263` — `Start-Process -FilePath 'opencode' ...`.
- `configuration/litellm/start-litellm.ps1:91` — `Start-Process -FilePath 'litellm' ...`
  (recorded by the prior lane; left as-is here).

The Linux twin `configuration/autostart/Start-AutoOSStack.sh:67` uses `nohup omniroute ...` in
a shell, where name resolution is the OS loader's job — not this Windows `Start-Process`
class; nothing to fix there.
