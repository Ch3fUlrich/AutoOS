# Lane ResilienceEnv — sane skip-on-repeated-429 launcher policy (ws-resilience-env-20260930)

Branch: `L1-backlog/ws-resilience-env-20260930`
Worktree: `AutoOS-ws-resenv`
Base sha: `c3e139ca98607c2cfe04d7e207956c73959c066b` (`git rev-parse HEAD` at start; clean).
No secrets below: env/key names and relative paths only. The one machine-local path
(`%APPDATA%\npm\node_modules`) is written in its env-var form, per redaction rule 1.

> **REVIEW NONCE: `RESENV-NONCE-lYoqt1JPO3`** — a reviewer must quote this string back,
> read from THIS file, to prove it read the evidence and not a summary.

---

## 0. What this lane did and did not do

- Applied the **rotation** env family (and one provider-breaker knob) to every
  tracked gateway-spawn site, respect-set and exactly once.
- **Did NOT** restart the shared gateway: the running process predates the change and
  keeps its old env until the next start (see §6.7).
- **Did NOT** touch the docker compose surface (§8).
- Base finding is **Freewire's proposal**, not a measured change: Freewire §4 recorded the
  values as a proposal and deliberately added no env anywhere
  (`git show L1-backlog/ws-freewire-20260930:docs/handoff/2026-09-30-laneFreeWire.md`).

---

## 1. Where the variables are read at startup (package source, file:line)

Installed package: **omniroute 3.8.50**,
`%APPDATA%\npm\node_modules\omniroute` (the gateway that runs on this host).

### 1.1 Rotation family — `open-sse/services/rotationConfig.ts`

The module header states the contract verbatim (lines 10–12):
`* rules as a runtime config, sourced from environment variables (so a supervising process can set`
`* them per launch) with an optional per-connection override ...`.

`buildFromEnv()` reads them once, at first use, and caches on `globalThis`:

```
  82: function buildFromEnv(): RotationConfig {
  83:   return {
  84:     enabled: envBool("OMNIROUTE_ROTATION_ENABLED", true),
  85:     rateLimitResetMs: envInt("OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS", 0, 0) * 1000,
  ...
  87:     rateLimit429: buildClass(
  88:       "OMNIROUTE_ROTATE_ON_429",
  89:       "OMNIROUTE_ROTATE_429_THRESHOLD",
  90:       "OMNIROUTE_ROTATE_429_WINDOW_SECONDS",
  91:       true
  92:     ),
```

Supporting lines:
- `61: function envInt(name, dflt, min = 0)` and `52: function envBool(name, dflt)` —
  `process.env[name]` read, empty ⇒ default.
- `50: const DEFAULT_WINDOW_MS = 120_000;`
- `75-78` buildClass: `threshold: envInt(thresholdEnv, 1, 1)` (default **1** = hop on the
  first error) and `windowMs: envInt(windowEnv, DEFAULT_WINDOW_MS / 1000, 1) * 1000`
  (default **120** s).
- `119-125: getGlobalRotationConfig()` parses once and stores the result on `globalThis` —
  a process-env read at startup, **no `.env` file load** for these keys.

### 1.2 Provider-breaker family — `open-sse/config/constants.ts`

`PROVIDER_PROFILES.apikey` (lines 259–278) reads `process.env` for the defaults:

```
 259:   apikey: {
 ...
 266:     providerFailureThreshold: envInt("OMNIROUTE_PROVIDER_BREAKER_API_KEY_FAILURE_THRESHOLD", 15), // Scaled for 500+ connections (was 5)
 267:     providerFailureWindowMs: envInt(
 268:       "OMNIROUTE_PROVIDER_BREAKER_API_KEY_FAILURE_WINDOW_MS",
 269:       1800000
 270:     ), // 30min window (was 20min)
 271:     providerCooldownMs: envInt("OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS", 600000), // 10min cooldown when threshold reached
 272:     degradationThreshold: envInt("OMNIROUTE_PROVIDER_BREAKER_API_KEY_DEGRADATION_THRESHOLD", 7),
 273:     maxBackoffMultiplier: envInt("OMNIROUTE_PROVIDER_BREAKER_API_KEY_MAX_BACKOFF_MULTIPLIER", 4),
 274:     backoffEscalationCount: envInt(
 275:       "OMNIROUTE_PROVIDER_BREAKER_API_KEY_BACKOFF_ESCALATION_COUNT",
 276:       3
 277:     ),
```

Freewire's §4 also found the provider-breaker **config** is already applied through
`configuration/omniroute/apply.ps1`'s `patch-api-resilience`
(`failureThreshold=2`, `degradationThreshold=1`, `resetTimeoutMs=30000`) — that is the API
surface, not the env default. This lane only sets the env **default** for
`OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS`.

---

## 2. Freewire's proposal (input, quoted)

From `L1-backlog/ws-freewire-20260930:docs/handoff/2026-09-30-laneFreeWire.md` §4:

```
OMNIROUTE_ROTATION_ENABLED=true
OMNIROUTE_ROTATE_ON_429=true
OMNIROUTE_ROTATE_429_THRESHOLD=3          # hold the first two 429s; rotate on the third
OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120
OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300
OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000
```

The same section's own caveat, kept honest here: *"The provider-breaker family is already
applied through `patch-api-resilience` ... The rotation family has no surface — it is read
from the gateway process env only, and the running gateway must be restarted to pick it up
(not done here). Recorded as a proposal, not claimed."*

---

## 3. Which file(s) supply the running gateway's env (proved, not assumed)

The gateway reads these from `process.env` at startup (§1). The only tracked surfaces that
assemble that env are the launchers that **spawn** it. Proof that the running gateway was
spawned by a launcher of exactly that shape:

```
$ Get-NetTCPConnection -LocalPort 20128 -State Listen
LocalAddress LocalPort OwningProcess
------------ --------- -------------
0.0.0.0          20128        126780

$ Get-CimInstance Win32_Process (the chain)
 pid 126780  parent 127428  "C:\Program Files\nodejs\node.exe" --dns-result-order=ipv4first
                            --max-old-space-size=4096 <npm-global>\omniroute\dist\server-ws.mjs
 pid 127428  parent  57340  "node" "<npm-global>\omniroute\bin\omniroute.mjs" --no-open --port 20128
 pid  57340  parent  61296  cmd.exe /c ""%APPDATA%\npm\omniroute.cmd" --no-open --port 20128"
 pid  61296  (gone)         — the parent that launched the .cmd shim has exited
```

`<npm-global>` = `%APPDATA%\npm\node_modules`. The argv `--no-open --port 20128` through the
resolved `%APPDATA%\npm\omniroute.cmd` shim is exactly what the PowerShell spawn sites emit
(`Start-Process -FilePath $omnirouteCmd -ArgumentList '--no-open', '--port', '20128'`):

- `configuration/start-stack.ps1:132` (non-docker launcher)
- `configuration/omniroute/apply.ps1:110` (also spawns when the gateway is down)
- `configuration/autostart/Start-AutoOSStack.ps1:66` (logon resume helper)

The bash twins spawn via `nohup omniroute ... &`, which likewise inherits the shell env, and
also assemble it:

- `configuration/start-stack.sh:80`
- `configuration/omniroute/apply.sh:449`

The docker stack assembles its env separately (`configuration/docker/ai-stack/compose.yml` +
`stack.env`); it is out of scope here (§8). **Conclusion:** for a native host the launcher is
the only in-repo surface, so the four named files **plus** the autostart helper (the same
spawn class, already treated by `tests/run-tests.ps1`'s launcher test) must carry the policy.

---

## 4. Values applied and why

| Variable | Value | Why |
|---|---|---|
| `OMNIROUTE_ROTATION_ENABLED` | `true` | Master rotation switch; shipped default is already `true` (`rotationConfig.ts:84`) — set explicitly to pin the policy against a future default flip. |
| `OMNIROUTE_ROTATE_ON_429` | `true` | 429 rotation class; shipped default `true` (`rotationConfig.ts:91`) — explicit for the same reason. |
| `OMNIROUTE_ROTATE_429_THRESHOLD` | `3` | **The core of "skip on repeated 429".** Shipped default is `1` (`buildClass`, line 77) = rotate on the *first* 429. `3` holds the first two transient 429s for the same leg in the window and rotates on the third, so a one-off throttle does not cost a hop while a persistently throttled leg is still skipped. |
| `OMNIROUTE_ROTATE_429_WINDOW_SECONDS` | `120` | The sliding window the threshold counts over. Equal to the shipped default (`DEFAULT_WINDOW_MS=120_000`, line 50) — set explicitly so "3 in 120 s" is visible in the launcher and pinned. |
| `OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS` | `300` | Cooldown applied to a rate-limited leg when the upstream gives no retry-after. `0` = engine default (line 16); `300` bounds the skip to 5 min instead of an open-ended/per-request retry. |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS` | `1800000` | Provider-level cooldown default; shipped `600000` (10 min, line 271). 30 min matches the `providerFailureWindowMs` default and only trips after 15 provider-level failures, so a hard-down provider is parked rather than retried every request. |

All six are respect-set: an operator value already in the environment or process env wins.

---

## 5. Files and exact lines changed

| File | Lines | Shape |
|---|---|---|
| `configuration/start-stack.ps1` | 111–133 | comment + six `if (-not $env:X) { $env:X = 'v' }` guards |
| `configuration/omniroute/apply.ps1` | 88–110 | same block (8-space indent) |
| `configuration/autostart/Start-AutoOSStack.ps1` | 44–66 | same block |
| `configuration/start-stack.sh` | 80–91 | comment + six `export X="${X:-v}"` |
| `configuration/omniroute/apply.sh` | 449–460 | same block |
| `tests/run-tests.ps1` | 9809–9844 | new `Test-Case` (fail-first) |
| `tests/linux/17-ai-routing.sh` | 2526–2551 | new `it` case |

---

## 6. Verification (every command, quoting the result)

### 6.1 Failing-first (test added before the fix)

```
$ pwsh -NoProfile -File tests\run-tests.ps1 -Filter 'repeated-429'
  X omniroute gateway launchers default the repeated-429 rotation policy, respect-set and once
      configuration\start-stack.ps1 does not respect a user-set OMNIROUTE_ROTATION_ENABLED
  passed 0   failed 1   skipped 0
Exited with code 1
```

### 6.2 Post-fix focused suites

```
$ pwsh -NoProfile -File tests\run-tests.ps1 -Filter 'repeated-429'
  passed 49   failed 0   skipped 0      # 48 assertions + 1 Pass

$ bash tests/run-tests.sh --filter '429 rotation policy'
  passed 1   failed 0   skipped 0

$ pwsh -NoProfile -File tests\run-tests.ps1 -Filter 'chat admission without clobbering'
  passed 27   failed 0   skipped 0      # the neighbouring admission case still green
```

### 6.3 Parse checks (0 errors)

```
PowerShell Parser::ParseFile, errors:
  configuration\start-stack.ps1: 0 parse errors
  configuration\omniroute\apply.ps1: 0 parse errors
  configuration\autostart\Start-AutoOSStack.ps1: 0 parse errors
  tests\run-tests.ps1: 0 parse errors

bash -n:
  start-stack.sh: 0
  apply.sh: 0
  17-ai-routing.sh: 0

$ shellcheck configuration/start-stack.sh configuration/omniroute/apply.sh
shellcheck: clean
```

### 6.4 Behavioural: the launcher exports the variables (child-process env dump)

The guard block was extracted from the real `configuration/start-stack.ps1` by AST
(`guards extracted: 6`), written to a child script that runs it **twice**, then run in a child
`pwsh`. The child inherits the parent's env; the guard writes into it:

```
--- run A: nothing preset (expect defaults) ---
OMNIROUTE_ROTATION_ENABLED=true
OMNIROUTE_ROTATE_ON_429=true
OMNIROUTE_ROTATE_429_THRESHOLD=3
OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120
OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300
OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000
--- run B: operator preset OMNIROUTE_ROTATE_429_THRESHOLD=9 (expect 9 kept) ---
OMNIROUTE_ROTATION_ENABLED=true
OMNIROUTE_ROTATE_ON_429=true
OMNIROUTE_ROTATE_429_THRESHOLD=9        <-- operator value survives (no clobber)
OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120
OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300
OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000
```

The bash guard (`export ...="${...:-default}"`) was exercised the same way — extracted from
`configuration/start-stack.sh` (`export lines extracted: 6`), sourced **twice** in a child
`bash` under `env -i`; run A gave the six defaults, run B (with
`OMNIROUTE_ROTATE_429_THRESHOLD=9`) kept `9`.

### 6.5 Double run ⇒ no duplicate lines, no clobber

- The child harnesses above ran each guard block **twice**; the dumped values are identical
  (env assignment is idempotent).
- Each of the six assignments appears **exactly once** per file (source grep, 1 per name per
  file; the committed suites assert this too), so a re-run/re-apply cannot accumulate a line.

### 6.6 Analyzer and dry run

- `Invoke-ScriptAnalyzer` on the three touched `.ps1` reports **only pre-existing** findings
  (`PSAvoidUsingWriteHost` throughout, `PSUseShouldProcessForStateChangingFunctions` for
  `New-FileBackup`, one pre-existing `KeysMissing`); the added lines introduce **no new
  finding** (no added `Write-Host`, no new rule category).
- `pwsh -NoProfile -File configuration\omniroute\apply.ps1 -DryRun` →
  `This is a dry run - nothing is registered, created or started.` …
  `Gateway OK on http://127.0.0.1:20128` … `apply.ps1 -DryRun exit: 0`.

### 6.7 The running gateway was NOT restarted (and why it matters)

The listener on 20128 (pid 126780) predates this change, so its env does **not** yet carry
the policy. These variables are read once at startup and cached on `globalThis`, so the
policy takes effect the next time a launcher spawns the gateway. Restarting the shared
gateway was forbidden by the directive and was not done.

### 6.8 Repo hygiene

`git status` clean after the commit; no secret, no username, no absolute user-home path in a
tracked file this lane touched (`git diff | Select-String 'C:\\Users|sk-…|192.168.|100.70.'`
→ no match).

---

## 7. Review (item 5)

_Filled after the nonce-gated reviewer verdict — see the DONE note for the verdict line._

---

## 8. Remains / out of scope (reported, not changed)

- **Docker surface.** `configuration/docker/ai-stack/compose.yml` and `stack.env.example`
  assemble the gateway container's env separately; not changed here (directive scoped to the
  launcher env, and the running gateway on this host is native).
- **`apply.ps1`/`apply.sh` gateway-spawn branches** carry the block because they *do* spawn
  the gateway when it is down (proved §3); they are not the usual path.
- **Restart.** Picking the policy up on this host needs a future gateway restart (operator
  action), not part of this lane.
