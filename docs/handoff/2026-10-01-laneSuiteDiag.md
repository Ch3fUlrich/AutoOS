# Lane SuiteDiag — the `usb custom-url downloads and verifies` "park" (2026-10-01)

**Type:** read-only diagnosis + proposal. No test file was edited, nothing pushed/merged.
**Author:** `suite-diag` (reader tier).
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-suite-diag`
**Branch:** `L1-backlog/ws-suite-diag-20260930`, base `origin/main` @ `e58274a8030625c19b739032d37aa82d2ec24a73`
**Shell:** `pwsh` 7.5.8 (`C:\Program Files\PowerShell\7\pwsh.exe`), Windows PowerShell 5.1 present.
**Claim of provenance:** every claim below is a command plus its exact output. Temp scripts live under `%TEMP%\opencode\`.

---

## TL;DR

The case **passes**; it is **not** a network block, not a timeout, and not a flake.

The stall is in the Windows test harness's HTTP fixture teardown:

> `Stop-AutoOSTestHttpServer` calls `Remove-Job -Job <server job> -Force`
> while that job is blocked inside `TcpListener.AcceptTcpClient()`
> (`tests/run-tests.ps1:2405`). `Remove-Job -Force` then blocks for **exactly
> 120.02 s** — PowerShell's background-job stop/close timeout. The job host
> process stays alive the whole time.

Consequences:

* **Every** case that calls `Start-AutoOSTestHttpServer` pays ~120 s, including
  cases with nothing to do with USB or the network.
* The suite has **13 server-starting cases** → ~**26 minutes** of pure teardown
  overhead in a full run. An observer whose own timeout is shorter than that
  sees "no summary line" and calls it a park.
* The suite *does* terminate: a previous full log ends with a normal summary
  (quoted in the appendix).

The test's actual download is `GET http://127.0.0.1:<port>/my%20image.iso` from a
loopback server and takes 0.06 s. There is no external network involved.

---

## 1. The case: what it is and what it downloads

`tests/run-tests.ps1:3508`:

```
3508: Test-Case 'usb custom: executing custom-url downloads and verifies against the supplied digest, and a second run is skipped' {
3509:     $fx = New-AutoOSCustomImageFixture
3510:     $srv = Start-AutoOSTestHttpServer -Directory $fx.Dir
3511:     $cache = Join-Path $fx.Dir 'cache'
3512:     $url = "http://127.0.0.1:$($srv.Port)/my%20image.iso"
...
3517:     $first = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan ... }
3518:     $second = Invoke-AutoOSCapturedConsole { Invoke-AutoOSUsbPlan ... }
...
3524:         Stop-AutoOSTestHttpServer $srv
```

* It creates a 25-byte throwaway file named `my image.iso` in a temp dir whose
  path contains a space, computes its SHA-256, starts a **loopback** HTTP server
  over that directory, then drives the USB plan twice: first downloads and
  verifies against the supplied digest, second is a cache-hit `skipped`.
* The URL is `http://127.0.0.1:<OS-assigned port>/my%20image.iso` — **loopback
  only, no internet.**
* The download helper is `Get-AutoOSRawDownload`
  (`lib/windows/AutoOS.Download.psm1:220`). For http(s) it runs
  (`lib/windows/AutoOS.Download.psm1:244`):
  ```
  curl.exe -fsSL --retry 3 --retry-delay 2 -o $OutFile -- $Uri
  ```
  so there are up to 3 retries with a 2 s delay, **no `--connect-timeout` and no
  `--max-time`**. The `Invoke-WebRequest` fallback
  (`lib/windows/AutoOS.Download.psm1:263`) has **no `-TimeoutSec`** either.
  (This is a real production-code gap for genuine multi-GB downloads, but it is
  *not* what parks the suite — see §3.)

The test's body logic, replicated faithfully outside the suite, runs in under a
second (§2.4).

---

## 2. Reproduction, bounded

All runs bounded with `Start-Process` + `$p.WaitForExit(ms)`; a parked child is
killed by PID with `taskkill /PID <pid> /T /F`. The suite prints its summary and
exits 0/1 from `tests/run-tests.ps1:9780-9792`.

### 2.1 Narrowest selector — the case under test

Command (inside `%TEMP%\opencode\repro-usb-custom-url.ps1`, which launches the
suite and times it):

```
pwsh -NoProfile -File "C:\...\AutoOS-ws-suite-diag\tests\run-tests.ps1" -Filter "executing custom-url"
```

Output:

```
PID=26008 DONE=True ELAPSED_SEC=124.2
EXITCODE=0
===== STDOUT =====
AutoOS Windows test suite  (C:\...\AutoOS-ws-suite-diag)
... group headers ...
-- usb safety
  + usb custom: executing custom-url downloads and verifies against the supplied digest, and a second run is skipped
-- static analysis
...
--------------------------------------------------------
  passed 1   failed 0   skipped 0
```

It **completes** (exit 0, summary present) — but takes ~124 s for one test.

### 2.2 Startup baseline vs the case (probe-timing)

```
[nomatch] filter='zzz-no-such-case-zzz' pid=55676 done=True sec=1.3  exit=0
[target]  filter='executing custom-url'  pid=82772 done=True sec=122.3 exit=0
```

Startup/imports = **1.3 s**. The case itself = **~121 s**. (Three independent
runs: 124.2 s, 122.3 s, 122.2 s — deterministic.)

### 2.3 The download and server are fast (probe-curl)

```
server start sec=0.3 port=54551 jobstate=Running
curl version: curl 8.21.0 (Windows) libcurl/8.21.0 ...
curl sec=0.06 exit=0 err=[]  downloaded=True size=25
```

So the 121 s is **not** the HTTP transfer, DNS, proxy, or a stalled socket.
(`NO_PROXY=127.0.0.1,localhost,::1` is set; curl reaches the loopback instantly.)

### 2.4 The case body in isolation (probe-steps) — sub-second

Replicating the case with the same module functions and the same helper code:

```
  New-AutoOSCustomImageFixture                   0,03s
  Start-AutoOSTestHttpServer                     0,43s
  New-AutoOSUsbPlan -DryRun                      0,09s
  Invoke-AutoOSUsbPlan #1 (download)             0,11s
  Invoke-AutoOSUsbPlan #2 (skip)                 0,01s
  first=[  - fetching http://127.0.0.1:56624/my%20image.iso
  + verified image: ...\cache\custom-url-4b54cdec333cd507.iso
  + Ready to boot: \\.\PHYSICALDRIVE5]
  second=[  - fetching ... skipped: custom-url-4b54cdec333cd507.iso already downloaded and verified ...
  + verified image: ...]
```

Total < 1 s. So the missing 120 s is **not in the case body**.

### 2.5 Sampling the process tree during a real run (probe-sampler)

The suite run took `elapsed=122.2s done=True exit=0`. Sampling descendants of
the suite process shows a single child `pwsh.exe` (the `Start-Job` host)
alive from t≈3.4 s to t≈120.7 s:

```
t=    3,4s pid=71700 pwsh.exe "C:\Program Files\PowerShell\7\pwsh.exe" -s -NoLogo -NoProfile -wd C:\Users\mauls\Documents\Code\AutoOS
t=  ...
t=  120,7s pid=71700 pwsh.exe "C:\Program Files\PowerShell\7\pwsh.exe" -s -NoLogo -NoProfile -wd C:\Users\mauls\Documents\Code\AutoOS
--- suite tail ---
  passed 1   failed 0   skipped 0
```

The job host lives for essentially the entire run and is only released at
process exit.

### 2.6 Isolation: `Remove-Job -Force` on a blocked `AcceptTcpClient` (probe-jobstop)

A minimal job that starts a `TcpListener`, writes its port, serves one curl
request, then blocks in `AcceptTcpClient()` again — i.e. exactly the fixture —
then the same teardown the suite uses:

```
srv start: 0.29s port=57693
curl fetch: 0.03s exit=0
--- timing Remove-Job -Force (what Stop-AutoOSTestHttpServer does) ---
Remove-Job -Force: 120.02s
total script: 121.27s
```

**Confirmed root cause: `Remove-Job -Force` on a job blocked in
`AcceptTcpClient()` blocks for 120.02 s.**

### 2.7 It is not specific to the USB case (probe-control)

A **different, non-usb** server case, `verified download: an http URL is streamed
to disk through curl.exe and verifies (http)` (`tests/run-tests.ps1:2454`):

```
[http-stream] filter='an http URL is streamed' pid=720 done=True sec=121.9 exit=0
  passed 1   failed 0   skipped 0
```

Same 120 s. The problem is the shared HTTP fixture, not the USB case.

### 2.8 It accumulates with server count (probe-accum)

`-Filter 'fetch mirror'` matches 3 cases, 2 of which start the fixture
(`New-AutoOSUsbFetchMirror`, `tests/run-tests.ps1:3298`):

```
filter='fetch mirror' pid=102748 done=True sec=244.9 exit=0
  passed 3   failed 0   skipped 0
```

**244.9 s ≈ 2 × 120 s + startup.** N server cases ≈ N × 120 s.

### 2.9 A candidate teardown fix removes the stall (probe-fix)

A stop-file + `$listener.Pending()` polling loop (the job exits on its own)
instead of a blocking `AcceptTcpClient()`:

```
srv start: 0.31s port=57118
curl fetch: 0.05s exit=0
Remove-Job -Force after stop-file: 0.02s
```

**0.02 s.** Validated (not applied).

---

## 3. Characterisation: network block, missing timeout, or flake?

**None of the three.** It is a deterministic, harness-level job-teardown stall.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Network block/timeout | **No** | URL is `127.0.0.1`; curl fetches in 0.06 s (§2.3); curl-to-dead-port fails in 2.1 s with a bounded connect timeout (§2.3 second half). |
| Missing timeout in the case | **No** | The case completes and passes; the stall is after its body, in `Remove-Job -Force` (§2.6). |
| Flake | **No** | Three runs at 124.2 / 122.3 / 122.2 s and an isolated 120.02 s — deterministic. |
| **Harness teardown** | **Yes** | `tests/run-tests.ps1:2449-2452` `Stop-AutoOSTestHttpServer` → `Remove-Job -Force`; job blocked at `tests/run-tests.ps1:2405` `$listener.AcceptTcpClient()`. |

Exact lines:

```
tests/run-tests.ps1:2404     while ($true) {
tests/run-tests.ps1:2405         $client = $listener.AcceptTcpClient()   <-- blocks; not interruptible
...
tests/run-tests.ps1:2449 function Stop-AutoOSTestHttpServer {
tests/run-tests.ps1:2450     param($Server)
tests/run-tests.ps1:2451     if ($Server -and $Server.Job) { Remove-Job -Job $Server.Job -Force -ErrorAction SilentlyContinue }   <-- waits 120 s
tests/run-tests.ps1:2452 }
```

The 120 s is PowerShell's background-job stop/close timeout; it is not an
infinite hang. That is why a full run eventually finishes with a summary — the
observer just has to wait ~26 min, which reads as a park.

Secondary, separate production finding (not the suite park): the real download
path has no wall-clock bound — `curl.exe -fsSL --retry 3 --retry-delay 2`
(`lib/windows/AutoOS.Download.psm1:244`, no `--max-time`) and
`Invoke-WebRequest ... -MaximumRedirection 10`
(`lib/windows/AutoOS.Download.psm1:263`, no `-TimeoutSec`). A genuinely stalled
vendor/CDN connection could hang a real 6 GB ISO download forever. Worth a
follow-up, but out of scope here.

---

## 4. Network-dependent cases (for a verification wave)

**There are no external-internet cases.** Every executed fetch goes to a
loopback server or a `file://` URI:

* `Get-AutoOSVerifiedFile` call sites use `ConvertTo-AutoOSTestFileUri`
  (`tests/run-tests.ps1:2327`) or `http://127.0.0.1:<port>`.
* `example.invalid` appears only inside plan-refusal assertions that never
  execute a fetch (`tests/run-tests.ps1:3480-3486`).
* The GPG test explicitly avoids a keyserver and a real key
  (`tests/run-tests.ps1:2550`).
* `Invoke-WebRequest .../api/health` is loopback (`tests/run-tests.ps1:8989`);
  the `Invoke-RestMethod` at `:9229` is inside a string fixture, not executed.

The cases that **start the loopback fixture** (each pays ~120 s in teardown,
`file:line` = the `Start` site):

| # | `file:line` (Start) | Case | Stop |
|---|---|---|---|
| 1 | `tests/run-tests.ps1:2460` | `verified download: an http URL is streamed to disk through curl.exe and verifies (http)` | `:2469` |
| 2 | `tests/run-tests.ps1:2476` | `verified download: an http 404 is a transport failure that leaves no .part behind (http)` | `:2484` |
| 3 | `tests/run-tests.ps1:3298` (via `New-AutoOSUsbFetchMirror`, caller `:3580`) | `usb: Invoke-AutoOSUsbFetchImage uses a healthy mirror before the canonical source ... (fetch mirror)` | `:3591` |
| 4 | `tests/run-tests.ps1:3298` (caller `:3608`) | `usb: Invoke-AutoOSUsbFetchImage skips a mirror whose bytes do not match the manifest ... (fetch mirror)` | `:3616` |
| 5 | `tests/run-tests.ps1:3510` | `usb custom: executing custom-url downloads and verifies ...` (this task) | `:3524` |
| 6-13 | `tests/run-tests.ps1:8986` (via `Start-AutoOSPruneGateway`; callers `:9002, :9021, :9040, :9060, :9084, :9133, :9145, :9188`) | the 8 `apply prune:` cases | `:8992, :9012, :9031, :9047, :9075, :9098, :9141, :9150, :9215` |

**13 server starts → ~26 min of teardown.** A `-Filter` that avoids the prune
group and the two `(http)` cases is cheap; a `-Filter usb` still includes rows
3-5 (~6 min).

Deterministic exclusion for a verification wave, if `-Filter` is unavailable:
the single substring common to all 13 is not clean (USB, http, prune).
`Start-AutoOSTestHttpServer` at `tests/run-tests.ps1:2405` is the one choke
point.

---

## 5. Options (proposed, not applied)

### (a) Fix the teardown — unblock the job so `Remove-Job` is instant  ⭐ recommended

Change the fixture to stop cooperatively. Minimal, localized to
`tests/run-tests.ps1` (server loop `:2404-2430`, helpers `:2389-2452`):

1. `Start-AutoOSTestHttpServer` creates a stop-file, passes it into the job, and
   returns it alongside `Job`/`Port`:
   ```
   while (-not (Test-Path -LiteralPath $stopFile)) {
       if ($listener.Pending()) {
           $client = $listener.AcceptTcpClient()
           ... existing serve block ...
       } else { Start-Sleep -Milliseconds 25 }
   }
   $listener.Stop()
   ```
2. `Stop-AutoOSTestHttpServer` writes the stop-file, then `Remove-Job -Force`
   (now instant), then deletes the stop-file.

Validated in isolation: `Remove-Job -Force after stop-file: 0.02s` (§2.9).
Return-shape stays compatible: callers already use `$srv.Port` / `$srv.Job` and
pass the object to the stop helper; `New-AutoOSUsbFetchMirror` wraps it
unchanged.

*Alternative shape:* keep the blocking accept, but have the job `break` when it
sees `GET /__autoos_test_stop__`, and make `Stop-AutoOSTestHttpServer` open one
connection to that path first. Fewer changes to the loop, adds a magic path.

**Trade-offs:** tiny polling loop during each fixture's life; touches the test
harness (the only place the bug lives). Restores the full suite to its real
runtime, so the AGENTS.md §7 full-suite gate becomes usable again. No coverage
lost. This is the only option that fixes the actual defect.

### (b) Env flag to skip these cases in agent runs

Add one guard in `Test-Case` (`tests/run-tests.ps1:51`), e.g.:
```
if ($env:AUTOOS_SKIP_SERVE_TESTS -eq '1' -and $Name -match '\(http\)|fetch mirror|executing custom-url|apply prune') {
    Skip 'serve-fixture tests skipped (AUTOOS_SKIP_SERVE_TESTS)'; return
}
```
**Trade-offs:** deterministic and fast for agents; but it is name-based and
brittle, and it removes the only coverage of the download/manifest/mirror/prune
paths. It papers over the defect rather than fixing it. Best kept only as a
stop-gap *after* (a) is rejected.

### (c) Accept `-Filter` runs as the gate and document it

No code change. Document that the full Windows suite carries ~26 min of fixed
teardown overhead and that agent verification uses disjoint `-Filter` shards
(e.g. `-Filter usb,catalog,...`) with a bounded `Start-Process` wrapper.

**Trade-offs:** zero risk, unblocks this run immediately; but it contradicts
AGENTS.md §7 ("the full Windows suite"), and shards can miss cross-shard
regressions. It also leaves the 120 s/job defect in the repo for the next
person. Weakest long-term option.

**Recommendation:** (a). It is small, validated, loses no coverage, and makes
the full-suite pass quotable again. (b) only if (a) cannot land this cycle;
(c) only as an interim note.

---

## 6. Reviewer (different family, nonce-gated)

*Pending — filled in after the review of the committed artefact.*

---

## Appendix — previous full-suite log

`%TEMP%\opencode\suite-admission-fix.log` (246 969 bytes, mtime 2026-09-30
23:09:11) **ends with a complete summary** — the suite does terminate:

```
--------------------------------------------------------
  passed 1748   failed 5   skipped 13

  failures:
    - registry: no generated file drifts
    - autoos-agent spawner unit tests: card routing, clients, depth
    - agent harness: the generator's unit tests pass
    - combos.json is valid, named and provider/model shaped
    - apply scripts refresh the catalog between registering and reading /v1/models
SUITE_EXIT=1
```

(`%TEMP%\opencode\prefix-suite.log` likewise ends with `passed 65 failed 5
skipped 1`.) This is consistent with a fixed per-job stall, not an infinite
park: the run finishes once the ~120 s teardown per server case has elapsed.
