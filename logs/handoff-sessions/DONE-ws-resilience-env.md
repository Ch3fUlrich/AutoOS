# DONE — ws-resilience-env-20260930 (lane ResilienceEnv, L2 fix worker under L0)

Verdict: **DONE**. Freewire's skip-on-repeated-429 policy — recorded there only as a
*proposal* — is now applied respect-set and idempotently in **every** tracked native gateway
env site (six launchers plus the systemd unit's `~/.omniroute/.env` writer); failing-first
tests added; parse/shellcheck/dry-run/behaviour verified; reviewed twice (first review FAIL
→ fix → re-review PASS); no gateway restart, no secrets, no hardcoded user paths.

## What changed

- Six policy env defaults, respect-set (operator value wins) and exactly once:
  `OMNIROUTE_ROTATION_ENABLED=true`, `OMNIROUTE_ROTATE_ON_429=true`,
  `OMNIROUTE_ROTATE_429_THRESHOLD=3`, `OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120`,
  `OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300`,
  `OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000`.
- Sites: `configuration/start-stack.ps1`, `configuration/omniroute/apply.ps1`,
  `configuration/autostart/Start-AutoOSStack.ps1`, `configuration/start-stack.sh`,
  `configuration/omniroute/apply.sh`, `configuration/autostart/Start-AutoOSStack.sh`
  (round 2), and `configuration/autostart/register-autostart.sh`, which defaults them in
  `~/.omniroute/.env` — the surface the `autoos-omniroute` systemd unit uses because it
  deliberately carries no `Environment=` (round 2).
- Tests: `tests/run-tests.ps1` (new `Test-Case`), `tests/linux/17-ai-routing.sh` and
  `tests/linux/34-ai-services.sh` (new `it` cases), all asserting respect-set + exactly-once.

## Commits (branch `L1-backlog/ws-resilience-env-20260930`, no push/merge/rebase)

- `51fd01d29d0aedc781ec637649ae5366864f9797` — fix(resilience): default the repeated-429
  rotation policy in every omniroute gateway launcher (7 files, +158).
- `3b0135e37653380a5051e083cbfb29aa21403f70` — docs(resilience): lane evidence + DONE note.
- `a80625c567fb507bb45942a9e14f4daf90226790` — fix(resilience): cover the autostart shell +
  systemd unit gateway paths (reviewer finding; 4 files, +98/−11).
- `518046a2ca9a406da0ecc1a3790e0087b7fda85c` — docs(resilience): correct the env-surface
  claims + record the review finding (round 2).
- Final docs commit (this evidence correction + this DONE note) follows.

Base sha: `c3e139ca98607c2cfe04d7e207956c73959c066b`.

## Where the vars are read (package source)

- Rotation family: `open-sse/services/rotationConfig.ts:84-110` (`buildFromEnv`,
  `getGlobalRotationConfig` caches on `globalThis`); defaults threshold 1, window 120 s,
  rate-limit reset 0.
- Provider-breaker family: `open-sse/config/constants.ts:251-277`
  (`PROVIDER_PROFILES.apikey`, cooldown default 600000).
- The process gets these from its **launch env**, or from an `.env` the loader reads:
  `bin/omniroute.mjs:102-169` `loadEnvFile()` reads `$DATA_DIR/.env`, `~/.omniroute/.env`,
  `cwd/.env`, `ROOT/.env`, filling `process.env[key]` only when `undefined` (line 140) — so
  the launch env wins, and the unit's `.env` is the fallback. Full quotes in the evidence doc.

## Which files supply the running gateway's env (proved)

The listener on `:20128` is `server-ws.mjs` ← `omniroute.mjs --no-open --port 20128` ←
`cmd.exe /c ""%APPDATA%\npm\omniroute.cmd" --no-open --port 20128"` (parent exited) — the
exact `Start-Process ... $omnirouteCmd` argv of the three `.ps1` spawn sites. The bash twins
spawn via `nohup` (inherit the shell env). The docker stack assembles its env separately
(out of scope). All seven native sites now carry the policy; the evidence doc §3 has the full
table (verified independently by the reviewer's own `--no-open --port 20128` sweep).

## Verification numbers (quoted in `docs/handoff/2026-10-01-laneResilienceEnv.md`)

- Failing-first: `-Filter 'repeated-429'` pre-fix → `passed 0 failed 1 skipped 0`, EXIT=1
  (`configuration\start-stack.ps1 does not respect a user-set OMNIROUTE_ROTATION_ENABLED`).
- Post-fix: `-Filter 'repeated-429'` → `passed 49 failed 0 skipped 0`; Linux
  `--filter 'rotation policy'` → `passed 2 failed 0`; `--filter 'register-autostart'` →
  `passed 11 failed 0`; neighbouring admission case → `passed 27 failed 0`.
- Parse errors = 0 on the four touched `.ps1`/suite file; `bash -n` = 0 on the six `.sh`;
  `shellcheck` clean on all four touched launchers.
- Behavioural child-process env dump of the real guard blocks (`start-stack.ps1` and
  `Start-AutoOSStack.sh`): clean run → all six defaults; operator preset `THRESHOLD=9` → `9`
  kept; block run/sourced twice → identical. `register-autostart.sh` sandbox: one append, one
  backup, operator `THRESHOLD=7` kept, second run skips.
- `apply.ps1 -DryRun` → `Gateway OK on http://127.0.0.1:20128`, EXIT=0, no spawn.
- ScriptAnalyzer: no new finding vs base (only the pre-existing `Write-Host` etc. baseline).
- No gateway restart (forbidden). `chat_admission_busy` / `Rate limit exceeded` observed: 0;
  backoffs: 0. Lane death: never.

## Review (item 5)

Reviewer: `t3-reviewer` subagent, model `omniroute/t3-driver-clean` (`opencode.jsonc:375`) —
a **different family** from the writer (`deepseek-v4.1-flash`).

- **Verdict 1 (session `ses_f09b55003ffeunIYKJ0bwlsRZ9`): FAIL.** It quoted the nonce and
  found two tracked native gateway-spawn sites the first pass missed
  (`configuration/autostart/Start-AutoOSStack.sh` and the `autoos-omniroute` systemd unit,
  whose env surface is `~/.omniroute/.env`), plus a false "no `.env` file load" claim in the
  evidence doc. Both correct. **Fixed in `a80625c`; the doc corrected in `518046a`.**
- **Verdict 2 (fresh session `ses_f09ac6f94ffeXasIXzU0zpSr75`): PASS.** It quoted the nonce,
  re-ran the spawn sweep itself, confirmed all seven sites carry the six names exactly once,
  the `.env` writer is respect-set with one append/one backup, the loader claim matches
  installed omniroute 3.8.50, `shellcheck`/`bash -n`/all three filters pass, and no test was
  weakened. Only a doc nit (stale §3 line numbers) — fixed.

## Remains (for L0)

- Merge decision for this branch. `apply.ps1`/`apply.sh`'s resilience block is the file
  lane-B edits; this lane's addition sits above the admission default and does not touch
  `patch-api-resilience`, so a merge conflict there is at worst trivial.
- Out of scope, reported not changed: the docker compose surface
  (`configuration/docker/ai-stack/compose.yml`, `stack.env.example`).
- Picking the policy up on this running host needs a future gateway restart (operator,
  not done).

## Redaction

- No secret, no real username, no absolute user-home path in any tracked file this lane
  introduced or changed. The observed shim/global paths are written in `%APPDATA%` /
  `~/.omniroute` env-var form.
- This DONE note added with `git add -f` (`logs/` is git-ignored).
