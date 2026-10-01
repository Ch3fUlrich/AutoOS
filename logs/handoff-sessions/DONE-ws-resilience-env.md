# DONE — ws-resilience-env-20260930 (lane ResilienceEnv, L2 fix worker under L0)

Verdict: **DONE** (pending the nonce-gated cross-family review, §5). Freewire's
skip-on-repeated-429 policy — recorded there only as a *proposal* — is now applied
respect-set and idempotently in every tracked gateway-spawn site; failing-first tests added;
parse/shellcheck/dry-run/behaviour verified; no gateway restart, no secrets, no hardcoded
user paths.

## What changed

- Six policy env defaults added to each gateway-spawn site, respect-set (operator value
  wins) and exactly once: `OMNIROUTE_ROTATION_ENABLED=true`, `OMNIROUTE_ROTATE_ON_429=true`,
  `OMNIROUTE_ROTATE_429_THRESHOLD=3`, `OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120`,
  `OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300`,
  `OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000`.
- Sites: `configuration/start-stack.ps1`, `configuration/omniroute/apply.ps1`,
  `configuration/autostart/Start-AutoOSStack.ps1`, `configuration/start-stack.sh`,
  `configuration/omniroute/apply.sh`.
- Tests: `tests/run-tests.ps1` (new `Test-Case`) and `tests/linux/17-ai-routing.sh`
  (new `it` case), both asserting respect-set + exactly-once for all six names.

## Commits (branch `L1-backlog/ws-resilience-env-20260930`, no push/merge/rebase)

- `51fd01d29d0aedc781ec637649ae5366864f9797` — fix(resilience): default the repeated-429
  rotation policy in every omniroute gateway launcher.
  `configuration/start-stack.ps1` +24, `configuration/omniroute/apply.ps1` +24,
  `configuration/autostart/Start-AutoOSStack.ps1` +24, `configuration/start-stack.sh` +12,
  `configuration/omniroute/apply.sh` +12, `tests/run-tests.ps1` +36,
  `tests/linux/17-ai-routing.sh` +26 (7 files, 158 insertions).
- Docs commit (this evidence + this DONE note) follows.

Base sha: `c3e139ca98607c2cfe04d7e207956c73959c066b`.

## Where the vars are read (package source)

- Rotation family: `open-sse/services/rotationConfig.ts:84-110` (`buildFromEnv`,
  `getGlobalRotationConfig` caches on `globalThis`); defaults threshold 1, window 120 s,
  rate-limit reset 0.
- Provider-breaker family: `open-sse/config/constants.ts:251-277`
  (`PROVIDER_PROFILES.apikey`, cooldown default 600000).
- Source contract: "sourced from environment variables (so a supervising process can set
  them per launch)" — `rotationConfig.ts:10-12`. Full quotes in the evidence doc.

## Which files supply the running gateway's env (proved)

The listener on `:20128` is `server-ws.mjs` ← `omniroute.mjs --no-open --port 20128` ←
`cmd.exe /c ""%APPDATA%\npm\omniroute.cmd" --no-open --port 20128"` (parent exited) — the
exact `Start-Process ... $omnirouteCmd` argv of the three `.ps1` spawn sites. The bash twins
spawn via `nohup` (also inherit the shell env). Docker assembles its env separately
(out of scope). Full chain in the evidence doc §3.

## Verification numbers (quoted in `docs/handoff/2026-10-01-laneResilienceEnv.md`)

- Failing-first: `-Filter 'repeated-429'` pre-fix → `passed 0 failed 1 skipped 0`, EXIT=1
  (`configuration\start-stack.ps1 does not respect a user-set OMNIROUTE_ROTATION_ENABLED`).
- Post-fix: `-Filter 'repeated-429'` → `passed 49 failed 0 skipped 0`; Linux
  `--filter '429 rotation policy'` → `passed 1 failed 0`; neighbouring admission case
  `-Filter 'chat admission without clobbering'` → `passed 27 failed 0`.
- Parse errors = 0 on the four touched `.ps1`/suite file; `bash -n` = 0 on the three `.sh`;
  `shellcheck` clean on both touched launchers.
- Behavioural child-process env dump of the real guard block: clean run → all six defaults;
  operator preset `THRESHOLD=9` → `9` kept (no clobber); block run/sourced twice →
  identical (idempotent). Each assignment appears exactly once per file.
- `apply.ps1 -DryRun` → `Gateway OK on http://127.0.0.1:20128`, EXIT=0, no spawn.
- ScriptAnalyzer: no new finding vs base (only the pre-existing `Write-Host` etc. baseline).
- No gateway restart (forbidden). `chat_admission_busy` / `Rate limit exceeded` observed: 0;
  backoffs: 0. Lane death: never.

## Review (item 5)

_Filled after the nonce-gated cross-family reviewer verdict (see the evidence doc §7)._

## Remains (for L0)

- Merge decision for this branch. The `apply.ps1`/`apply.sh` resilience block is the file
  lane-B edits; this lane's addition is immediately above the admission default and does not
  touch `patch-api-resilience`, so a merge conflict there is at worst trivial.
- Out of scope, reported not changed: the docker compose surface
  (`configuration/docker/ai-stack/compose.yml`, `stack.env.example`).
- Picking the policy up on this running host needs a future gateway restart (operator,
  not done).

## Redaction

- No secret, no real username, no absolute user-home path in any tracked file this lane
  introduced or changed. The observed shim/global path is written in `%APPDATA%` env-var
  form.
- This DONE note added with `git add -f` (`logs/` is git-ignored).
