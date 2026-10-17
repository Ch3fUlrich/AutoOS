# DONE — ws-suite-fix-20260930

**Date:** 2026-10-01
**Lane:** L1-backlog, `suite-fix`
**Base:** `e58274a` (origin/main == main at worktree creation)
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-suitefix`
**Branch:** `L1-backlog/ws-suite-fix-20260930`
**Commits:** `4548e07` (fix) + this docs commit

---

## Deliverable

Implements option **(a)** from the diagnosis `docs/handoff/2026-10-01-laneSuiteDiag.md`
(branch `L1-backlog/ws-suite-diag-20260930`, commit `038d93e`): make the Windows test
harness's HTTP fixture tear down cooperatively instead of paying PowerShell's 120 s job
stop/close timeout.

One file, `tests/run-tests.ps1` (+50 / -4), confined to `Start-AutoOSTestHttpServer` /
`Stop-AutoOSTestHttpServer` plus one new bounded-teardown regression case. No existing case,
coverage or assertion changed.

## Root cause (confirmed by the diagnosis, re-measured here)

`Stop-AutoOSTestHttpServer` called `Remove-Job -Force` on a job parked in
`TcpListener.AcceptTcpClient()`. That native call is uninterruptible, so the stop blocked for
120.02 s. 13 server-starting cases ⇒ ~26 min of pure teardown; the suite read as a park.

## Fix

The job polls a stop-file and calls `AcceptTcpClient()` only when `Pending()` reports a queued
connection; `Stop-AutoOSTestHttpServer` writes the stop-file (then removes it), so the job
leaves its accept loop on its own and `Remove-Job` returns at once. `$using:stopFile` is used
rather than a third `-ArgumentList` param, to keep `Invoke-ScriptAnalyzer` at parity.

## Measurements

| metric | before | after |
|---|---|---|
| one-fixture teardown, `pwsh` 7.5.8 | 120.01 s | 0.03 s |
| one-fixture teardown, Windows PowerShell 5.1 | 120.02 s | 0.02 s |
| focused 14 server-starting cases, `pwsh` | ~28 min (est. 14 × 120 s) | 25.0 s (`passed 35 failed 0 skipped 0`) |
| focused 14 server-starting cases, 5.1 | ~28 min (est. 14 × 120 s) | 21.9 s (`passed 35 failed 0 skipped 0`) |
| **full suite, `pwsh`** | **2112.5 s (35.2 min)** | **671.9 s (11.2 min)** |

Failing-first, at the harness level: a throwaway copy of the suite with the new regression
case but the baseline helpers ran `-Filter 'test http server'` and failed
`teardown took 120,0s, expected < 15s` (`passed 1 failed 1`, 121.5 s). The copy was deleted,
never committed.

## Verification

| check | result |
|---|---|
| parse errors, `pwsh` + 5.1 | 0 / 0 |
| `Invoke-ScriptAnalyzer` on `tests/run-tests.ps1` | 17 = 17 issues, identical rule histogram |
| focused server cases green, both shells | `passed 35 failed 0 skipped 0` (5.1: exit 0; pwsh: exit 0) |
| full suite: branch vs detached baseline `e58274a` | failures `NONE` new / `NONE` gone (same 5 pre-existing) |
| full suite summary: baseline → branch | `passed 1730 failed 5 skipped 13` → `passed 1732 failed 5 skipped 13` (+2 = the new case's assertions) |

The five pre-existing failures (`registry: no generated file drifts`, `autoos-agent spawner
unit tests`, `agent harness: the generator's unit tests pass`, `combos.json is valid ...`,
`apply scripts refresh the catalog ...`) are environmental at the base and unrelated; they are
identical on both sides. Note the diagnosis appendix quoted `passed 1748` from a different
worktree/base; this lane's baseline is a fresh detached `e58274a` run, so its `1730` is the
correct control for the comparison.

## Reviewer

- **Cross-family reviewer:** `t3-reviewer` on `omniroute/t3-driver-clean` (t3 family,
  "t3 cheap-driver-128k"), session `ses_f0a075b05ffeM28inisEdmgeVV`.
- **Nonce:** `REVIEW-NONCE=95908da238dae795` — returned verbatim (gate passed).
- **Verdict:** APPROVED-WITH-NOTES. Both notes (retain probe artefact; soften the
  wall-clock-flake wording) are applied. Details in
  `docs/handoff/2026-10-01-laneSuiteFix.md` §6.

## Open items for L0

1. **Follow-up (out of scope, from the diagnosis):** the production download path has no
   wall-clock bound — `lib/windows/AutoOS.Download.psm1:244`
   (`curl.exe -fsSL --retry 3 --retry-delay 2`, no `--max-time`/`--connect-timeout`) and the
   `Invoke-WebRequest` fallback `:263` (no `-TimeoutSec`). A genuinely stalled vendor/CDN
   connection could hang a real ISO download. Worth its own lane.
2. Nothing else: the fix is self-contained in the test harness.
