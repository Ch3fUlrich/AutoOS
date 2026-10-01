# Lane dl-timeout — wall-clock bounds on the Windows production download path

- **Date:** 2026-10-01
- **Branch:** `L1-backlog/ws-dl-timeout-20260930`
- **Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-dltimeout`
- **Base:** `96e5a53` (suite-fixture teardown-fix tip)
- **Commits:** `c408e186` (fix), `ed1ed397` (tests)

## Defect (verified, not assumed)

`Select-String`-confirmed before any edit — `Get-AutoOSRawDownload` in
`lib/windows/AutoOS.Download.psm1` called both network transports with no
wall-clock bound:

| site (pre-fix) | transport | before | after |
|---|---|---|---|
| `AutoOS.Download.psm1:244` | `curl.exe` | `-fsSL --retry 3 --retry-delay 2 -o … -- $Uri` | `-fsSL --connect-timeout 30 --max-time <total> --retry 3 …` |
| `AutoOS.Download.psm1:263` | `Invoke-WebRequest` | `-Uri … -OutFile … -UseBasicParsing -MaximumRedirection 10` | `… -MaximumRedirection 10 -TimeoutSec <total>` |
| `AutoOS.Install.psm1:634` | `Invoke-WebRequest` (Nerd Font) | no `-TimeoutSec` | `-TimeoutSec $script:AutoOSFetchTimeoutSec` |
| `AutoOS.Install.psm1:3595` | `Invoke-WebRequest` (Qoder installer) | no `-TimeoutSec` | `-TimeoutSec $script:AutoOSFetchTimeoutSec` |
| `AutoOS.Usb.psm1:1552` | `Invoke-RestMethod` (Ventoy release API) | no `-TimeoutSec` | `-TimeoutSec $script:AutoOSUsbFetchTimeoutSec` |
| `AutoOS.Usb.psm1:1559` | `Invoke-WebRequest` (Ventoy zip) | no `-TimeoutSec` | `-TimeoutSec $script:AutoOSUsbFetchTimeoutSec` |
| `AutoOS.Usb.psm1:1565` | `Invoke-WebRequest` (sha256.txt) | no `-TimeoutSec` | `-TimeoutSec $script:AutoOSUsbFetchTimeoutSec` |

A stalled mirror could therefore hang a provision forever. This is the same
class of unbounded wait the 2026-10-01 suite-fixture teardown fix removed
(`4548e079`).

## The bounds and why these numbers

- `$script:AutoOSDownloadConnectTimeoutSec = 30` — connection establishment.
- `$script:AutoOSDownloadTimeoutSec = 3600` — total transfer, overridable by
  `AUTOOS_DOWNLOAD_TIMEOUT_SEC` (validated; a malformed value throws rather
  than silently removing the bound).
- `$script:AutoOSFetchTimeoutSec = 300` and
  `$script:AutoOSUsbFetchTimeoutSec = 300` — small ancillary files (font,
  installer script, Ventoy release assets).

**Deviation from the task's `e.g. --max-time 300`:** the very same helper
streams multi-GB installer ISOs (`AutoOS.Usb.psm1:1275` calls
`Get-AutoOSVerifiedFile` for the image; this module's own history records a
6 GB image). A 300 s total would abort a legitimate 6 GB transfer on any link
slower than ~160 Mbit/s — a regression, and the task says "preserve every
other behaviour". 3600 s still bounds a genuine stall and only fails below
~13 Mbit/s, which `AUTOOS_DOWNLOAD_TIMEOUT_SEC` can raise. curl's `--max-time`
is per attempt, so `--retry 3` makes the worst case ~4 attempts plus delays —
still bounded. The connect bound is the task's 30 s exactly.

## Review

Nonce-gated, cross-family reviewer (`NONCE DLT-7F3A9C21`): agent `t3-reviewer`,
model/family **`omniroute/t3-driver-clean` / "t3 cheap-driver-128k"** —
different family from the authoring model (`deepseek-v4.1-flash`).

**Verdict: APPROVED-WITH-NOTES. Must-fix: none.**

Notes raised and disposition:

1. *`--max-time` is per attempt, so the comment's "total-transfer ceiling"
   overstates* → comment corrected to say per-attempt / ~4 attempts worst case
   (`AutoOS.Download.psm1` bounds block).
2. *Stall test could pass on a fast connection-refused without exercising the
   bound* → assertion now also requires `elapsed >= 1 s` (the injected bound is
   2 s), so a sub-second failure cannot satisfy it.
3. *Structural guard is a tripwire, not exhaustive (splats/aliases/line
   continuations)* → comment added stating the known false negatives and that
   the behavioural stall test is what actually exercises the bound.
4. *Three duplicated `300` constants is a minor DRY nit* → accepted; the
   constants live in three independent modules with no shared dependency, and
   the reviewer did not consider it worth coupling them.


## Failing-first evidence

Against the **pre-fix** modules (detached baseline worktree, my new tests
copied in):

```
=== PRE-FIX structural guard (filter lib\windows) ===
  X every production network fetch in lib\windows carries a wall-clock bound
      AutoOS.Download.psm1:244 curl without --max-time; AutoOS.Download.psm1:263 Invoke-WebRequest without -TimeoutSec; AutoOS.Install.psm1:634 Invoke-WebRequest without -TimeoutSec; AutoOS.Install.psm1:3595 Invoke-WebRequest without -TimeoutSec; AutoOS.Usb.psm1:1552 Invoke-RestMethod without -TimeoutSec; AutoOS.Usb.psm1:1559 Invoke-WebRequest without -TimeoutSec; AutoOS.Usb.psm1:1565 Invoke-WebRequest without -TimeoutSec
  passed 0   failed 1   skipped 0
=== PRE-FIX stall test (filter stalled, external 40s guard) ===
STILL RUNNING after 40s => HANGS (pre-fix defect demonstrated)
```

Post-fix:

```
OK : stalled  [ + verified download: a stalled endpoint fails within the wall-clock bound instead of hanging (timeout) || passed 6 failed 0 skipped 0 ]
OK : wall-clock [ + verified download: a stalled endpoint fails within the wall-clock bound instead of hanging (timeout) || + every production network fetch in lib\windows carries a wall-clock bound || passed 2 failed 0 skipped 0 ]
```

The stall test injects `AUTOOS_DOWNLOAD_TIMEOUT_SEC=2`, points
`Get-AutoOSVerifiedFile` at a loopback server in `-Hang` mode (accepts the
connection, reads the request, never answers), and asserts a fast
transport-failure with no `.part` left behind. Pre-fix there is no bound to
honour, so it hangs — the defect.

## Parsing, analyzer

```
=== pwsh parse ===            AutoOS.Download.psm1: 0, AutoOS.Install.psm1: 0, AutoOS.Usb.psm1: 0, run-tests.ps1: 0  TOTAL=0
=== powershell parse ===      AutoOS.Download.psm1: 0, AutoOS.Install.psm1: 0, AutoOS.Usb.psm1: 0, run-tests.ps1: 0  TOTAL=0
ANALYZER PARITY: IDENTICAL rule counts
```

`Invoke-ScriptAnalyzer` rule-name counts for the four touched files are
byte-identical to the baseline worktree's (no new findings). The new private
`Get-AutoOSDownloadTimeoutSec` is not exported, so it does not add a
`PSProvideCommentHelp` finding; the fixture's switch is read with
`[bool]$Hang` in the function scope and consumed via `$using:hangRequested`,
so it does not add a `PSReviewUnusedParameter` finding.

## Full-suite comparison vs detached baseline

Both worktrees ran the full suite with `pwsh -NoProfile -File tests\run-tests.ps1`.

| run | passed | failed | skipped |
|---|---|---|---|
| baseline `96e5a53` (detached worktree) | 1732 | 5 | 13 |
| this branch `e87648df` | 1734 | 5 | 13 |

The failing set is identical before and after — the same 5 pre-existing,
environment-dependent failures (`registry: no generated file drifts`;
`autoos-agent spawner unit tests`; `agent harness: the generator's unit tests`;
`combos.json is valid…`; `apply scripts refresh the catalog…`). The +2 passes
are exactly the two new tests (the stall test and the structural guard);
skipped is unchanged. **No new suite failures.** The Linux suite is untouched
by this change (only `lib/windows/*.psm1` and the Windows `tests/run-tests.ps1`
were modified — `git diff --name-only` lists no `.sh`).

## Out-of-scope findings (recorded, not fixed here)

- `lib/linux/download.sh`'s main fetch is **already bounded**: it routes curl
  through `run()` → `lib/linux/process.py`, which enforces
  `AUTOOS_INSTALL_TIMEOUT_SECONDS` (default 1800 s) and kills the process
  group. Its direct signature `curl` (`download.sh:88`) and the many direct
  `curl` calls in `lib/linux/install.sh` are unbounded, but they are outside
  this lane's Windows scope — for L1 to route.
- `setup.ps1` was not scanned by the structural guard; the guard covers the
  shipped `lib/windows/*.psm1` modules named by the defect.
- The reviewer found unbounded fetches in imported infrastructure —
  `infra/…setup-agent-memory.ps1:214` and `infra/…setup-sync.ps1:176`. Those
  files are vendored infra with their own rules (AGENTS.md §2) and are not part
  of the `lib/windows` production path; recorded for L1 to route.

