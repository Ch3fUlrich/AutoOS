# DONE — ws-dl-timeout (L1-backlog/ws-dl-timeout-20260930)

**Status:** complete — fix, tests, review follow-up, doc and this note all committed; full-suite comparison below.

## What was done

Bound every unbounded production download on Windows in wall-clock time.

- `lib/windows/AutoOS.Download.psm1` — the defect:
  - `$script:AutoOSDownloadConnectTimeoutSec = 30`, `$script:AutoOSDownloadTimeoutSec = 3600`
    (constants + justification at the top).
  - curl: `--connect-timeout 30 --max-time <total>`; `Invoke-WebRequest -TimeoutSec <total>`.
  - `Get-AutoOSDownloadTimeoutSec` (private) is the `AUTOOS_DOWNLOAD_TIMEOUT_SEC` test/operator seam.
- `lib/windows/AutoOS.Install.psm1` — `$script:AutoOSFetchTimeoutSec = 300`; Nerd Font + Qoder installer fetches.
- `lib/windows/AutoOS.Usb.psm1` — `$script:AutoOSUsbFetchTimeoutSec = 300`; Ventoy release API + zip + sha256.
- `tests/run-tests.ps1` — `-Hang` fixture mode; failing-first stall test; structural guard over `lib/windows/*.psm1`.

## Commits

```
c408e186 fix(download): bound every Windows production network fetch in wall-clock time
ed1ed397 tests: prove a stalled download fails within the wall-clock bound
e87648df test(download): tighten the stall test and state the guard's limits
2964c8ea docs: record the dl-timeout bounds, evidence, review and baseline comparison
```

## Evidence (exact output in `docs/handoff/2026-10-01-laneDlTimeout.md`)

- Pre-fix structural guard **fails**, naming all 7 unbounded sites; pre-fix stall test **hangs** (>40 s).
- Post-fix both pass; injected 2 s bound → fast transport failure, no `.part`.
- Parse errors 0 under **both** `pwsh` 7.5.8 and `powershell` 5.1.
- `Invoke-ScriptAnalyzer` rule counts identical to baseline (parity).
- `git status` clean; only `.ps1`/`.psm1` touched; no secret, no username path, no binary.

## Scope note

"Every unbounded production download" was read as every direct Windows
production fetch (the named Download module plus Install/Usb). Linux is out of
scope and mostly already bounded via `process.py`; its remaining direct curls
are recorded as a finding for L1, not silently ignored.

## Full-suite comparison

Full Windows suite, both trees (`pwsh -NoProfile -File tests\run-tests.ps1`):

| run | passed | failed | skipped |
|---|---|---|---|
| baseline `96e5a53` (detached) | 1732 | 5 | 13 |
| this branch `e87648df` | 1734 | 5 | 13 |

Same 5 pre-existing failures in both; the +2 passes are exactly the two new
tests. **No new suite failures.** Linux suite untouched (no `.sh` changed).

## Review

Cross-family, nonce-gated (`NONCE DLT-7F3A9C21`): agent `t3-reviewer`, model
**`omniroute/t3-driver-clean`** ("t3 cheap-driver-128k"), not the authoring
family (`deepseek-v4.1-flash`). Verdict: **APPROVED-WITH-NOTES**, no must-fix.
Its four notes were all addressed (per-attempt comment, `elapsed >= 1 s` in
the stall test, tripwire caveat on the structural guard, DRY nit accepted) —
see `docs/handoff/2026-10-01-laneDlTimeout.md`.
