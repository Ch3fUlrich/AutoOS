# Lane SuiteFix — cooperative teardown for the Windows HTTP test fixture (2026-10-01)

**Type:** implementation + measurement. The fix is committed on this branch; nothing pushed/merged.
**Author:** `suite-fix`.
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-suitefix`
**Branch:** `L1-backlog/ws-suite-fix-20260930`, base `origin/main` @ `e58274a8030625c19b739032d37aa82d2ec24a73`
**Diagnosis this implements:** branch `L1-backlog/ws-suite-diag-20260930`, commit `038d93e`,
`docs/handoff/2026-10-01-laneSuiteDiag.md` (option **(a)**).
**Claim of provenance:** every claim below is a command plus its exact output. A Windows
username appearing in captured output is redacted to `<user>` (AGENTS.md §7). Decimal commas
in quoted `{N2}`/`{N1}` output are the host locale; the numbers are unaltered.

---

## TL;DR

`Stop-AutoOSTestHttpServer` used to call `Remove-Job -Force` on a job parked in
`TcpListener.AcceptTcpClient()`. That native call cannot be interrupted, so PowerShell's job
stop/close timeout blocked **every** stop for ~120 s; with 13 server-starting cases a full
Windows suite carried ~26 min of pure teardown overhead and read as a park.

The job now polls a stop-file and calls `AcceptTcpClient()` only when `Pending()` reports a
queued connection. `Stop-AutoOSTestHttpServer` writes the stop-file, the job leaves its loop
on its own, and `Remove-Job` returns at once.

* Same one-request fixture, teardown: **120.01 s → 0.03 s** (pwsh), **120.02 s → 0.02 s** (Windows PowerShell 5.1).
* Focused run of all 14 server-starting cases: **~28 min before → 25.0 s** (pwsh) / **21.9 s** (5.1), all green.
* A bounded-teardown regression case fails against the old helpers
  (`teardown took 120,0s, expected < 15s`) and passes with these.
* No case, coverage or assertion of any existing case changed.

---

## 1. The change

One file: `tests/run-tests.ps1` (commit `4548e07`), `+50 / -4`, confined to the HTTP-server
helper/teardown and one new regression case.

```diff
-    # Returns @{ Job; Port }; the caller must Stop-AutoOSTestHttpServer it.
+    # Returns @{ Job; Port; StopFile }; the caller must Stop-AutoOSTestHttpServer it.
     param([Parameter(Mandatory)][string]$Directory)
     $portFile = Join-Path ([IO.Path]::GetTempPath()) ('aos_port_' + [Guid]::NewGuid().ToString('N'))
+    $stopFile = Join-Path ([IO.Path]::GetTempPath()) ('aos_stop_' + [Guid]::NewGuid().ToString('N'))
     $job = Start-Job -ArgumentList $Directory, $portFile -ScriptBlock {
         param($dir, $portFile)
         ...
-        while ($true) {
+        while (-not (Test-Path -LiteralPath $using:stopFile)) {
+            if (-not $listener.Pending()) { Start-Sleep -Milliseconds 25; continue }
             $client = $listener.AcceptTcpClient()
             ...
         }
+        $listener.Stop()
     }
     ...
-    @{ Job = $job; Port = $port }
+    @{ Job = $job; Port = $port; StopFile = $stopFile }
 }

 function Stop-AutoOSTestHttpServer {
     param($Server)
-    if ($Server -and $Server.Job) { Remove-Job -Job $Server.Job -Force -ErrorAction SilentlyContinue }
+    if ($Server -and $Server.Job) {
+        if ($Server.StopFile) { Set-Content -LiteralPath $Server.StopFile -Value 'stop' -ErrorAction SilentlyContinue }
+        Remove-Job -Job $Server.Job -Force -ErrorAction SilentlyContinue
+        if ($Server.StopFile) { Remove-Item -LiteralPath $Server.StopFile -Force -ErrorAction SilentlyContinue }
+    }
 }
```

`$using:stopFile` (rather than a third `-ArgumentList` parameter) is deliberate: it keeps
`Invoke-ScriptAnalyzer` at parity — a third `param()` in the job would add one more
`PSUseUsingScopeModifierInNewRunspaces` diagnostic that the two existing params already
trigger (a known false positive for `-ArgumentList`).

The new regression case asserts one request is actually served through the poll loop, then
that `Stop-AutoOSTestHttpServer` returns in under 15 s. The stopwatch wraps only the stop call
(the 5 s request timeout is outside it); the measured value is 0.02–0.03 s and the bug is
120 s, so 15 s sits far outside machine noise. Any wall-clock assertion can in principle
flake; at a 500× margin this one is as safe as a bound gets.

---

## 2. Failing first — the before/after teardown, from the real helper text

`Stop-AutoOSTestHttpServer` / `Start-AutoOSTestHttpServer` were extracted **verbatim** from
each revision's file (regex over the two `function` blocks) and timed with one served
request, so the number is the code under review, not a paraphrase. The script is
`%TEMP%\opencode\teardown-probe.ps1` on the author host; the retained second run is
`logs/handoff-sessions/suitefix-teardown-probe.log`:

```
BEFORE_pwsh TEARDOWN_SEC=120,03 SERVED=System.Byte[]
AFTER_pwsh TEARDOWN_SEC=0,02 SERVED=System.Byte[]
BEFORE_winps51 TEARDOWN_SEC=120,02 SERVED=System.Byte[]
AFTER_winps51 TEARDOWN_SEC=0,02 SERVED=System.Byte[]
```

The first run agreed: `120,01 / 0,03 / 120,02 / 0,02`. (`SERVED=System.Byte[]` is expected:
the fixture sends no `Content-Type`, so `Invoke-WebRequest.Content` is bytes. The regression
case normalises it before comparing.)

A **harness-level** failing-first run: a throwaway copy of the suite carrying the new
regression case but the two **baseline** helpers was run with `-Filter 'test http server'`:

```
  + test http server: Stop-AutoOSTestHttpServer returns promptly, never the 120 s job stop timeout
  X test http server: Stop-AutoOSTestHttpServer returns promptly, never the 120 s job stop timeout
      teardown took 120,0s, expected < 15s
...
  passed 1   failed 1   skipped 0
RED_RUN_ELAPSED_SEC=121,5 EXIT=1
```

The temporary copy was deleted immediately; it was never committed.

---

## 3. Focused green, both shells

All 14 server-starting cases (the new regression case + the 13 the diagnosis listed), on the
fixed branch:

`-Filter 'test http server,an http URL is streamed,executing custom-url,apply prune,fetch mirror,an http 404'`

| shell | summary | wall |
|---|---|---|
| `pwsh` 7.5.8 | `passed 35   failed 0   skipped 0` (exit 0) | 25,0 s |
| Windows PowerShell 5.1 | `passed 35   failed 0   skipped 0` (exit 0) | 21,9 s |

Same set before the fix is ~14 × 120 s ≈ **28 min** of teardown alone.

---

## 4. Static checks

* **Parse errors = 0**, both shells:
  `pwsh : PARSE_ERRORS=0` / `powershell : PARSE_ERRORS=0`
  (via `[System.Management.Automation.Language.Parser]::ParseFile`).
* **`Invoke-ScriptAnalyzer` parity** on `tests/run-tests.ps1`, suite's own rule exclusions:
  branch **17** issues vs baseline **17**, identical rule histogram
  (`PSAvoidAssignmentToAutomaticVariable x2, PSAvoidUsingEmptyCatchBlock x1,
  PSReviewUnusedParameter x6, PSUseApprovedVerbs x1, PSUseDeclaredVarsMoreThanAssignments x5,
  PSUseUsingScopeModifierInNewRunspaces x2`). Line numbers shift; no new diagnostic.
* BOM/CRLF preserved in the working copy (`EF BB BF`, CRLF).

---

## 5. Full-suite comparison vs detached baseline `origin/main`

Both full runs use the same method as the previous lane: a detached worktree at `origin/main`
(`AutoOS-ws-suitefix-baseline` @ `e58274a`), `pwsh` 7.5.8, output redirected to a log, wall
time from `[Diagnostics.Stopwatch]` around the child `pwsh`.

| run | worktree | summary | wall |
|---|---|---|---|
| baseline | `origin/main` @ `e58274a` | `passed 1730   failed 5   skipped 13` (`SUITE_EXIT=1`) | **2112.5 s** (35.2 min) |
| fixed | `L1-backlog/ws-suite-fix-20260930` @ `4548e07` | `passed 1732   failed 5   skipped 13` (`SUITE_EXIT=1`) | **671.9 s** (11.2 min) |

**Failure sets are identical.** `new failures (in fixed, not baseline): NONE`,
`disappeared failures (in baseline, not fixed): NONE`. Both runs fail the same five:

```
registry: no generated file drifts
autoos-agent spawner unit tests: card routing, clients, depth
agent harness: the generator's unit tests pass
combos.json is valid, named and provider/model shaped
apply scripts refresh the catalog between registering and reading /v1/models
```

These five are pre-existing at the base (the diagnosis appendix records the same five) and
are untouched by this change; the focused runs show every server-starting case green.

`passed` is **+2** on the branch: the new regression case contributes two assertions. `skipped`
is unchanged at 13.

The wall-time drop is `2112.5 - 671.9 = 1440.6 s` (24.0 min), a **3.14×** speedup — the ~26 min
of `13 × 120 s` teardown overhead the diagnosis predicted, removed.

---

## 6. Reviewer (different family, nonce-gated)

**Reviewer:** `t3-reviewer` subagent, model route **`omniroute/t3-driver-clean`** (t3 family,
"t3 cheap-driver-128k") — a different family from the author (`deepseek-v4.1-flash`).
**Session:** `ses_f0a075b05ffeM28inisEdmgeVV`
**Artefacts reviewed:** commits `4548e07` (fix) and `73007e3` (docs).
**Nonce:** `REVIEW-NONCE=95908da238dae795` — returned verbatim (gate passed).
**Verdict: APPROVED-WITH-NOTES.**

The reviewer independently reproduced: the commit scoping (`git show --stat`; only
`tests/run-tests.ps1` in the fix commit), the confinement of the hunks to the two helpers plus
one added case, `$using:` job scoping in both shells, parse errors `0 / 0`, `PSScriptAnalyzer`
parity `17 = 17` with an identical rule histogram, the BOM/CRLF bytes, and the full-suite
summaries and failure lists from the two retained logs. It confirmed no test semantics changed
and that no caller of the fixture (direct, mirror, or prune) is broken by the added key.

Notes, both non-blocking and both applied:

1. The probe / failing-first / focused-run outputs were asserted in text but their artefacts
   were not retained. **Applied:** the probe re-run is now retained as
   `logs/handoff-sessions/suitefix-teardown-probe.log`, and §2 names the script path.
2. §1's "the bound cannot flake on a slow machine" was stronger than provable for any
   wall-clock assertion. **Applied:** reworded to state the margin and that it wraps only the
   stop call.

One caveat the reviewer raised is worth keeping: reproducing the `17/17` analyzer parity
requires the baseline **bytes** as stored (`git cat-file`, BOM intact); a `Set-Content`
round-trip drops the `PSReviewUnusedParameter` on `$Filter` and shows `16`. That is a
re-checking artefact, not a repo defect.
