# DONE — ws-suite-diag-20260930

**Date:** 2026-10-01
**Lane:** L1-backlog (reader/diagnosis tier)
**Base:** `origin/main` @ `e58274a8030625c19b739032d37aa82d2ec24a73`
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-suite-diag`
**Branch:** `L1-backlog/ws-suite-diag-20260930`
**Commit:** _(filled at commit time)_

---

## Deliverable

`docs/handoff/2026-10-01-laneSuiteDiag.md` — read-only diagnosis + proposal for the
`usb custom-url downloads and verifies` "park". No test file was edited, nothing was
pushed/merged.

## Verdict in one line

The case **passes**; the apparent park is a **harness teardown stall**, not a network
block, timeout or flake: `Stop-AutoOSTestHttpServer`
(`tests/run-tests.ps1:2449-2452`) calls `Remove-Job -Force` on a job blocked at
`$listener.AcceptTcpClient()` (`tests/run-tests.ps1:2405`), which blocks for exactly
**120.02 s**. 13 server-starting cases → ~26 min of teardown, which an observer with a
shorter timeout reads as a park. The suite does terminate with a normal summary.

## Evidence (command + exact output; full detail in the doc)

| Claim | Command (temp scripts in `%TEMP%\opencode\`) | Output |
|---|---|---|
| Target case passes, bounded | `pwsh -File tests/run-tests.ps1 -Filter "executing custom-url"` | `ELAPSED_SEC=124.2 EXITCODE=0`, `passed 1 failed 0` |
| Startup baseline | probe-timing | `[nomatch] sec=1.3` vs `[target] sec=122.3` |
| Loopback fetch is fast | probe-curl | `curl sec=0.06 downloaded=True size=25`, `server start sec=0.3` |
| Case body in isolation | probe-steps | total `< 1 s` |
| Cause isolated | probe-jobstop | `Remove-Job -Force: 120.02s` |
| Not USB-specific | probe-control | `[http-stream] sec=121.9 exit=0` |
| Accumulates per server | probe-accum | `filter='fetch mirror' sec=244.9` (~2×120) |
| Candidate fix validated | probe-fix | `Remove-Job -Force after stop-file: 0.02s` |
| Suite terminates | `%TEMP%\opencode\suite-admission-fix.log` tail | `passed 1748 failed 5 skipped 13`, `SUITE_EXIT=1` |

## Options (proposed, not applied)

- **(a) Fix the teardown** — stop-file + `$listener.Pending()` polling loop; validated
  0.02 s teardown, no coverage lost. **Recommended.**
- **(b) Env skip flag** in `Test-Case` — fast but name-based/brittle; removes coverage.
- **(c) Accept `-Filter` shards as the gate** — zero risk, contradicts AGENTS.md §7.

## Scope compliance

- Read-only: no changes under `tests/` or `lib/`; only `docs/handoff/` + this note.
- No push/merge/rebase/checkout; worktree left clean.
- Every started child killed by PID (`taskkill /PID <pid> /T /F`); all runs bounded.

## Review record

- **Writer:** suite-diag (reader tier)
- **Cross-family reviewer:** _pending — filled after nonce-gated review_
- **Verdict:** _pending_
