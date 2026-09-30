# Lane F2 — chat_admission_busy fix (ws-omniroute-20260930)

Branch: `L1-backlog/ws-gw-admission-20260930`. Gateway source (unmodified):
`C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute`.
Gateway: `http://127.0.0.1:20128`. No secrets below (key/lane names only).

## 1. KNOBS (gateway source, path:line + literal values)

All in `src/shared/middleware/chatBodyAdmission.ts` (env-only; read once at
module import; NO settings-route/sqlite/dashboard knob — `settings.ts` has no
admission fields; restart required).

| # | Knob | Location | Literal |
|---|------|----------|---------|
| a | Busy message (byte stage) | `chatBodyAdmission.ts:669` | `"Chat admission capacity is temporarily unavailable. Retry shortly."` |
| a | Busy code (both stages) | `chatBodyAdmission.ts:671`, `:693` | `"chat_admission_busy"` |
| a | Busy message (structural stage) | `chatBodyAdmission.ts:691` | `"Structurally heavy chat request capacity is busy; retry shortly."` |
| a | Shed log text | `chatBodyAdmission.ts:210` | `"structural chat admission shed (chat_admission_busy)"` (logger `chat-admission`) |
| a | Shed records (`queue_timeout`) | `chatBodyAdmission.ts:407` (window exhausted / queueMs=0 immediate), `:464` (parked-wait timeout; waiter removed at `:456` first, hence `waiting=0`) | `recordShed("queue_timeout", sessionKey)` |
| b | Queue-wait default | `chatBodyAdmission.ts:56-59` | `parseNonNegativeInt(process.env.OMNIROUTE_CHAT_ADMISSION_QUEUE_MS, 2000)` → **2000 ms** |
| b | Byte-stage wiring | `src/shared/middleware/withChatAdmission.ts:32` | `queueMs: options.queueMs ?? CHAT_ADMISSION_QUEUE_MAX_MS` |
| b | Structural-stage wiring (running build) | `dist/.build/next/server/chunks/src_1j9_4tw._.js` (~index 2817) | `admitChatStructure(l, d.lease, {sessionId:r, queueMs:g.CHAT_ADMISSION_QUEUE_MAX_MS, signal:e.signal})` — route parses body → structural admission → model-alias → injection guard |
| c | Max heavy slots | `chatBodyAdmission.ts:44-47` | `parsePositiveInt(process.env.OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT, 1)` → **default 1** |
| c | Healthy-heap headroom | `chatBodyAdmission.ts:119-122` | `parseNonNegativeInt(process.env.OMNIROUTE_CHAT_ADMISSION_HEALTHY_HEADROOM, CHAT_MAX_HEAVY_IN_FLIGHT)` → **default = maxHeavy** |

Effective default capacity on a healthy heap: 1 primary + 1 headroom = **2**
concurrent heavy; the 3rd parks ≤2000 ms then sheds `queue_timeout`.
(`activeHeavy` in shed lines counts only the primary lease; headroom usage is
invisible there — sheds at `activeHeavy=1` mean primary + headroom both held.)
The adaptive-admission runtime (`open-sse/services/admission/runtime.ts:33-43`)
defaults to `mode: "shadow"` (observe-only) — the structural gate above is the
enforcer. `CHAT_HEAVY_MESSAGE_COUNT` default 200 (`:74-77`); measurement bodies
use 210 messages / 16 KB (< 256 KB `CHAT_LARGE_BODY_BYTES`), so the structural
stage is the binding one.

## 2. MEASUREMENTS (`measure-admission.ps1 -Model t2-worker -Stream $true`, 210 msgs)

| Run | Command date (+02:00) | Result |
|-----|----------------------|--------|
| before-3 | `.\measure-admission.ps1 -Concurrency 3 -Label before-3 -MaxTokens 500` @ 2026-09-30T15:30:57 | **2×200, 1×503** `chat_admission_busy` @2098 ms |
| before-4 | `.\measure-admission.ps1 -Concurrency 4 -Label before-4 -MaxTokens 500` @ 2026-09-30T15:31:05 | **1×200, 3×503** `chat_admission_busy` @~2110 ms |
| after2-3 | same, `-Label after2-3` @ 2026-09-30T15:39:25 | **3×200, 0 rejected** (each ~13 s, fully overlapping) |
| after3-4 | same, `-Label after3-4` @ 2026-09-30T15:41:36 | **4/4 admitted past the gate, 0 `chat_admission_busy`**; all 4 then failed upstream with 401 `invalid_api_key` (expired provider grants — see §5) |

Log correlation (`C:\Users\mauls\.omniroute\logs\application\app.log`):
shed-line count 407 → 411 across the before-runs (exactly my 4 rejections;
`13:31:00Z` ×1, `13:31:08Z` ×3, incl. `waiting=2/queuedBytes=524288` park
evidence). Last 2 sheds `13:34:58Z` (operator lane on unfixed restart).
**Zero admission sheds since the fix took effect (`13:39:18Z`).**
Slot release (no leak): every run admitted fresh requests; shed lines never
show `activeHeavy` growing (always 1, transient).

## 3. FIX (config-only, no code patch)

- Knob: `OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT`: **1 → 4**.
- Set: USER-scope environment (`HKCU\Environment`), 2026-09-30T15:32:39+02:00
  (verified read-back `4`). `OMNIROUTE_CHAT_ADMISSION_HEALTHY_HEADROOM` left
  unset → auto-follows to 4. New capacity: 4 primary + 4 headroom = 8
  concurrent heavy on a healthy heap — covers ≥3 streams + burst spare on a
  65 GB-free host.
- Why this knob: sheds show `activeHeavy=1` (primary full) with waiters
  parking the full 2000 ms then timing out — pure capacity starvation, not
  heap pressure (`queuedBytes` park budget never the cause: no
  `queued_bytes_budget` sheds). Raising the slot count is exactly what the
  evidence supports; lengthening the queue would only slow-fail the same
  streams.
- `~/.omniroute/.env` was NOT edited (shell access to that secrets path is
  denied; process env wins over `.env` fill per `bin/omniroute.mjs:140`
  `if (process.env[key] === undefined)`).
- No patch: config expresses the fix. (`options.queueMs ?? 0` fallbacks in
  src are dead defaults — the running build always passes
  `CHAT_ADMISSION_QUEUE_MAX_MS`.)

Restart (env read at import → restart mandatory). Pitfall found: children
inherit the spawner's stale env block, so the first restart did NOT pick up
the registry value (after-3 run still shed). Fix: set the var in the spawning
shell AND keep the registry value for durability. Verbatim commands:

```powershell
Stop-Process -Id 58296
$env:OMNIROUTE_CHAT_MAX_HEAVY_IN_FLIGHT='4'; Start-Process -FilePath 'C:\Users\mauls\AppData\Roaming\npm\omniroute.cmd' -ArgumentList '--no-open', '--port', '20128' -WindowStyle Hidden -WorkingDirectory $env:USERPROFILE
```

(An intermediate `ProcessStartInfo` + `cmd.exe /c` spawn attempt at
15:35:51+02:00 never produced a listener — abandoned; the `Start-Process`
form above is proven: health 200 ~8 s after launch, PID 121652.)
Verification after final restart: `/api/health` 200 (`13:39:18Z`) + single
chat 200. Old default-config process fully stopped before each start (port
verified refusing first).

## 4. PROOF (same runs, before → after)

- 3-concurrent heavy streams: **2×200 + 1×503** (`before-3`) →
  **3×200 + 0 rejected** (`after2-3`, request overlap ~13 s each).
- 4-concurrent heavy streams: **1×200 + 3×503** (`before-4`) →
  **4 admitted, 0 `chat_admission_busy`** (`after3-4`; upstream 401s only).
- Gateway log: sheds stop at the fix; no `chat_admission_busy` responses
  from any post-fix request.

## 5. OPEN ITEM — provider grants expired (operator action needed, NOT admission)

`after3-4` legs report (401 `invalid_api_key`): gemini OAuth invalid;
antigravity / scaleway / nebius / deepseek (+1): "All N connection(s)
authentication expired — please reconnect in the dashboard". Boot-time
`TOKEN_REFRESH` failures (`13:39:29–37Z`) tripped one 30-min breaker;
single-probe 200 recovered `~13:41:30Z` via a surviving leg. History shows
decay predates this lane (opencode-zen breakers since 09-22, vertex 09:38Z
today); the old gateway coasted on in-memory tokens and the restart forced
re-auth from expired grants. **Operator: reconnect provider accounts in the
OmniRoute dashboard.** Re-running the 4-wide proof after reconnect is
recommended. Emission sites untouched; `configuration/omniroute/combos.json`
untouched.
