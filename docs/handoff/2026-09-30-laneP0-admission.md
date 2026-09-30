# Lane P0 — admission persistence + start-stack fix (ws-omniroute-20260930)

Branch: `L1-backlog/ws-p0-admission-fix-20260930` (worktree `AutoOS-ws-p0`, cut from main `d08f7f2`).
Gateway source (unmodified): `%APPDATA%\npm\node_modules\omniroute` v3.8.50.
Live gateway: `http://127.0.0.1:20128`. No secrets below (key/lane names only).

## 0. ROUTE HEALTH (recorded verbatim per brief)

Default t2-worker combo is all-legs 401 today:
`vertex/gemini-3.8-flash: auth — [401] ... Expected OAuth 2 access token ...;
gemini/gemini-3.8-flash: auth — [401] ...;
antigravity/gemini-3.7-flash-high / scw/qwen3-235b / nebius GLM:
All connection(s) authentication expired (+2 more)`, ses_f0d6d1814ffetB5CgjQYXrMU56 2026-09-30.
This lane runs on the direct model; shell probes hit the gateway over HTTP regardless.
Provider legs flap: this lane's own probes got 4×200 once, 4×401 one minute later (§3).

## 1. KNOB + LIVE EFFECT (measured, not assumed)

- Gate: `chatBodyAdmission.ts:44-47`
  `parsePositiveInt(process.env.OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT, 1)` → default 1,
  read once at module import (top-level `export const` — restart mandatory).
- Headroom: `chatBodyAdmission.ts:119-122` defaults to maxHeavy → default capacity 1+1=2.
- Queue: `chatBodyAdmission.ts:56-59` default 2000 ms. Busy code `chat_admission_busy`
  at `:671`, `:693`; shed log at `:210`.
- Startup env precedence (`bin/omniroute.mjs:112-118,140`): `$DATA_DIR/.env`,
  default-data-dir `.env` (≈`~/.omniroute/.env`), cwd `.env`, repo `.env` —
  but process env wins (`if (process.env[key] === undefined)`). So the spawner's
  environment (launcher) and USER-registry env both beat any `.env` file.
- Live process changed mid-lane: F2's PID 121652 (started 15:39:10 local) is gone;
  listener is now PID 132912, started 15:52:47 local (13:52:47Z), health 200.
  Restart party unknown (parent chain ends at a `px` launcher, not this lane).
  USER-registry `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT` reads back `4` (F2's value, durable).
- Behavioral proof the live process carries the knob (§3 run verify1):
  4 concurrent heavy streams each holding a slot ≥7.3 s, all 200.
  Under default 1+1=2 the 3rd/4th would park the full 2000 ms then shed 503 —
  they did not. Live effect confirmed.

## 2. INDEPENDENT RE-VERIFY (this lane's probes, key via env/file in-memory, names only)

Heavy = 210 messages (> `CHAT_HEAVY_MESSAGE_COUNT` 200), `stream=true`,
model `t2-worker`, `max_tokens=300`. Probe script ran from TEMP, deleted after.

| Run (UTC) | Concurrency | Result | Elapsed |
|---|---|---|---|
| verify1 13:54:33Z | 4 | 4×200, 0 `chat_admission_busy` | 7387–7710 ms, fully overlapping |
| verify2 13:54:54Z | 4 | 4×401 `invalid_api_key` (expired provider grants, post-dispatch only → gate passed), 0 `chat_admission_busy` | 1047–1517 ms |

- Gateway shed log (`...\.omniroute\logs\application\app.log`):
  415 shed lines total, latest 13:34:58Z (pre-fix); **0 sheds in 13:39:18Z–13:56Z**
  incl. across both probes (shed-delta 0). Zero sheds since F2's fix took effect.
- verify1 discriminates (7.5 s overlap ≫ 2000 ms queue); verify2 corroborates.
- `chat_admission_busy` count this lane: 0 → backoff count 0 (no 60–120 s backoffs needed).

## 3. PERSISTENCE (env-only knob → now in launchers)

F2 set USER-registry env = 4 (effective on this host: launcher respects set values).
This lane adds spawner-side defaults (idempotent, respect-a-set-value, never clobber):

- `configuration/start-stack.ps1`: `if (-not $env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT) { = '8' }`
- `configuration/start-stack.sh`: `: "${OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT:=8}"; export`
- `configuration/omniroute/apply.ps1` / `apply.sh`: same pattern at gateway-start block.
- Effective on this host: registry `4` wins → 4+4=8 heavy slots; fresh hosts get 8+8=16.
  Both cover ≥4 concurrent streams with burst spare.
- `~/.omniroute/.env` NOT edited: shell access to that secrets-adjacent path is denied,
  and per `omniroute.mjs:140` process env beats `.env` fill anyway — launcher env
  is the higher-precedence, verifiable mechanism.
- Prior-attempt artifacts found in worktree on arrival: `configuration/omniroute/admission.env`
  (379 B — content NOT verifiable: tooling denies all access to `*.env` paths as
  secrets-adjacent, and it cannot be removed for the same reason → left untracked,
  excluded from commit; redundant with the launcher change in any case), `docs/handoff/2026-09-30-laneP0-admission-before.md`
  (4×503 @~2.1 s default-config evidence, PID 134804 — kept, predates this lane,
  provenance not re-verified), `docs/handoff/2026-09-30-laneP0-admission-after.md`
  (all-TODO template → deleted, superseded by this doc).
- No user-owned files modified → no `.autoos-backup-*` needed (rule covers profiles/configs;
  only repo files in this lane worktree changed).

## 4. START-STACK SHIM FIX

- Measured: `Get-Command omniroute*` → bare `omniroute` resolves to
  `...\npm\omniroute.ps1` (ExternalScript) ahead of `omniroute.cmd` (Application);
  `Start-Process -FilePath 'omniroute'` cannot launch a `.ps1` as a Win32 app.
- Fix (`configuration/start-stack.ps1`, gateway-start block): resolve
  `omniroute.cmd` via PATH with `$env:APPDATA` fallback (no hardcoded user path):
  `$omnirouteCmd = (Get-Command omniroute.cmd ...).Source; if (-not ...) { Join-Path $env:APPDATA 'npm\omniroute.cmd' }`
  then `Start-Process -FilePath $omnirouteCmd ...`.
- Verify: `PSParser::Tokenize` parse-errors=0; dry-run `start-stack.ps1 -App none`
  → `Gateway OK on http://127.0.0.1:20128`, no spawn. Live-spawn path NOT executed
  (restart forbidden — gateway serves lanes); resolution logic verified in isolation
  (`resolved=...\npm\omniroute.cmd, exists=True`).
- Same bare-`omniroute` pattern remains in `configuration/omniroute/apply.ps1`
  → follow-up for L0 (out of this lane's item-3 scope).

## 5. F2 REVIEW (item 4)

- Attempt: fresh cross-family `t3-reviewer` leaf on commit `c41de4a`
  (writer: lane F2, operator-identity commit). FAILED at spawn — recorded verbatim:
  `scw/mistral-small-3.2-24b-instruct-2506: auth — [scaleway] All 1 connection(s)
  authentication expired — please reconnect in the dashboard (HTTP 401);
  nebius/zai-org/GLM-5.2: auth — [nebius] All 1 connection(s) authentication expired
  (HTTP 401); mistral/mistral-code-latest: auth — [mistral] All 1 connection(s)
  authentication expired (HTTP 401); deepseek/deepseek-flash: auth — [deepseek]
  All 1 connection(s) authentication expired (HTTP 401);
  meta-api/muse-spark-1.3-contributor: auth — [openai-compatible-chat-conn:d9427825]
  All 2 connection(s) authentication expired (HTTP 401)`
  (sessionID ses_f0d6625d7ffeRY0y2yCJTF1H0W). No healthy route exists;
  retry would hit the same expired grants → not retried, per token discipline.
- Reconciliation: no reviewer verdict obtainable (writer F2 / reviewer NONE / verdict NONE).
  Substituted independent behavioral re-verification (§2: verify1 4×200 @7.5 s overlap
  + shed-log delta 0 = the commit's core claim reproduced on the live gateway)
  plus self-review of the commit diff (docs+scripts only, `combos.json` untouched,
  no secrets/user paths in hunks, restart commands match the proven form).
  No defect in F2's change found by this lane; F2's open items (provider reconnect,
  post-reconnect 4-wide re-run) stand and are extended: verify1 already re-ran the
  4-wide proof with 200s, so only the operator reconnect remains.
