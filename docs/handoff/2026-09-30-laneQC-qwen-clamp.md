# Lane QC v4 — OmniRoute max_tokens clamp for `qwen3-235b-a22b-instruct-2507`

Run: `ws-omniroute-20260930` (L0 = workstation-L1-main). Lane: **L2 lane QC v4 ("qwen clamp")**.
Worktree: `AutoOS-worktrees/AutoOS-ws-qwenclamp`, branch `L1-backlog/ws-qwenclamp-20260930` at `d08f7f2`.
Gateway: OmniRoute v3.8.50, live `http://127.0.0.1:20128`, log
`C:\Users\<user>\.omniroute\logs\application\app.log`. All times UTC 2026-09-30. No secret quoted.

**Files this lane claims** (do not touch without me): `configuration/omniroute/capability-overrides.json`,
`configuration/omniroute/apply-capability-overrides.ps1`, `configuration/omniroute/patch-gateway-clamp.ps1`,
`configuration/omniroute/patches/0001-clamp-max-tokens.patch`, `configuration/omniroute/verify-clamp.mjs`,
`docs/handoff/2026-09-30-laneQC-qwen-clamp.md`, `logs/handoff-sessions/DONE-ws-qwenclamp.md`.
Nothing in `combos.json`, no vertex transform, no admission logic, no scaleway credentials.

---

## 0. TL;DR

- The per-model output cap is **already live** (config-first done 14:32:21Z, before the 14:37–14:51Z
  live-apply window): `scaleway/qwen3-235b-a22b-instruct-2507  max_output_tokens = 16384`.
- It did not clamp, because the gateway's only output clamp is gated on
  `capabilities.supportsThinking === true` — an *instruct* model never passes.
- **Fix delivered**: un-gate that clamp (config keeps the per-model scope) + formalise the cap in the
  repo. Applied to the real gateway bundle (§2). Unit-proofed, gateway-targeted, syntax-checked (§3).
- **Reviewer verdict: reject of the first draft**, with a correct, decisive finding — the first patch
  targeted `dist/open-sse/mcp-server/server.js` (the MCP-stdio bundle), which the HTTP gateway does
  **not** load. Reconciled: the patch now targets the Next standalone build the gateway runs.
- **Residual gap (documented, not hidden):** direct `provider/model` calls take a different code path
  from combos and are **not** clamped by this change. The mission's named vehicle is the `t2-worker`
  combo, which *is* fixed. See §5.

---

## 1. Root cause (file:line, measured)

Canonical compiled source: `…\omniroute\dist\open-sse\mcp-server\server.js`.
Live gateway target: `…\omniroute\dist\.build\next\**` (Next standalone; process is
`node …\dist\server-ws.mjs`, pid measured via `Get-NetTCPConnection -LocalPort 20128` → `Win32_Process`).

1. **The only output clamp** is `resolveReasoningBufferedMaxTokens` (`server.js:93495`),
   gated at **`server.js:93500`**:
   ```js
   const capabilities = getResolvedModelCapabilities(modelStr);
   if (capabilities.supportsThinking !== true) return null;   // <-- the gate
   const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));
   if (current > maxOutputTokens) return maxOutputTokens;      // the clamp
   ```
   In the gateway bundle the same code is minified but name-preserving (turbopack does not mangle
   module exports), e.g. `_1mq9y97._.js`:
   `resolveReasoningBufferedMaxTokens",0,function(e,t,r={}){…let a=(0,n.getResolvedModelCapabilities)(e);if(!0!==a.supportsThinking)return null;let s=i((0,n.getExplicitModelOutputCap)(e));…}`

2. **Called only from combo dispatch** — `server.js:115667` (priority) and `:116801` (round-robin);
   `grep -n resolveReasoningBufferedMaxTokens server.js` → exactly those two. In the gateway those
   call sites are e.g. `let t=(0,W.toPositiveInteger)(e.max_tokens),r=(0,W.resolveReasoningBufferedMaxTokens)(h,e.max_tokens,{enabled:eI});…` (`_1mq9y97._.js`).

3. **Per-model cap source** — `getExplicitModelOutputCap` (`server.js:34492-34507`):
   capability override `max_output_tokens` → synced `limit_output` → registry `maxOutputTokens` →
   static spec. The Scaleway registry entry (`server.js:20571-20588`) declares **no** `maxOutputTokens`.

4. **Per-model cap store** — `model_capability_overrides`, key `max_output_tokens`
   (`server.js:34042-34120`; `getModelCapabilityOverride` at `:34085`), through
   `PATCH /api/model-capability-overrides {target,key,value}` — the analogue of the repo's
   `context_length` mechanism.

**Measured live (config present, no clamp):** `GET /api/model-capability-overrides` returns
`{"target":"scaleway/qwen3-235b-a22b-instruct-2507","key":"max_output_tokens","value":16384,"refreshedAt":"2026-09-30 14:32:21"}`.
The repro (max_tokens 32768 → upstream 400 "…limited to 16384…") still occurred after that.

**Direct calls bypass the combo machinery** (why the combo-only clamp never helped them): a direct
`POST /v1/chat/completions | free-ai/qwen7b | 1 msgs` at 14:58:29Z logged
`tag=HTTP` → `tag=ROUTING Provider: free-ai, Model: qwen7b` → `tag=AUTH`, with no `tag=CHAT Combo`
(combos log `tag=CHAT Combo "t2-worker" [priority] with 10 models`).

---

## 2. Fix (config + minimal patch)

### 2a. Config — `configuration/omniroute/capability-overrides.json`
One row, applied by `apply-capability-overrides.ps1` (standalone; new file, no conflict with another
lane's edits to `apply.ps1`/`apply.sh`). **Target spelling is the resolved provider `scaleway/…`, not
the routing alias `scw/…`** — `getModelCapabilityOverride` builds its key from the resolved provider
(`server.js:34085-34099`); the `scw`→`scaleway` alias is resolved earlier. The JSON carries that
warning so nobody "fixes" it to `scw/…`.
Measured (this lane): `-DryRun` → `would ensure scaleway/qwen3-235b-a22b-instruct-2507 max_output_tokens = 16384`;
real run → `already 16384` (idempotent). No restart needed for the override itself.

### 2b. Patch — `configuration/omniroute/patches/0001-clamp-max-tokens.patch`
Applied by `patch-gateway-clamp.ps1` to **both** copies of the source, with per-file backups:
1. `dist/open-sse/mcp-server/server.js` (canonical form): un-gate the clamp + clamp the body in
   `executeModelUnit`.
2. **every gateway Next chunk carrying the gate** (exactly **4** today: `_04g0p_r`, `_0o50usg`,
   `_0wr-zm3`, `_1mq9y97` under `dist/.build/next/server/chunks/`): name-agnostic regex removes
   `let <v>=(0,<m>.getResolvedModelCapabilities)(<x>);if(!0!==<v>.supportsThinking)return null;`.

Effect: a model with an explicit cap now clamps; a model with no cap still returns `null` (untouched),
so this is a **per-model** change, not a global default. Idempotent; every changed file backed up to
`<file>.autoos-backup-<ts>`.

---

## 3. Proof (as far as the Scaleway state allows)

### 3a. Clamp decision — unit-proven, and gateway-targeted (no restart)
`configuration/omniroute/verify-clamp.mjs` — extracts the shipped `resolveReasoningBufferedMaxTokens`
text from the canonical bundle and evaluates it with stubs, then scans the whole gateway build.

Before patch:
```
PART A: FAIL over-limit 32768 -> 16384 (got null), FAIL 16, FAIL at-cap, FAIL string
PART B: FAIL - 4 gateway chunks still carry the supportsThinking gate
exit=2  (patched: false)
```
After patch:
```
PART A: PASS over-limit 32768 -> 16384; PASS 16 -> 16; PASS at cap; PASS "32768";
        PASS no cap -> null; PASS enabled=false -> null; PASS outgoing never exceeds cap
PART B: PASS - no gateway chunk still carries the gate (14213 js files scanned)
ALL PASS   exit=0
```
Safety: `node --check` exit 0 on all 4 patched gateway chunks and the mcp bundle. Patched clamp text
(`_1mq9y97._.js`):
`function(e,t,r={}){if(!1===r.enabled)return null;let o=i(t);if(null===o)return null;let s=i((0,n.getExplicitModelOutputCap)(e));return null===s?null:o>s?s:o}`

### 3b. Replay of the 32768 request — verbatim (blocked by credits; stated)
```
UTC 2026-09-30T15:05:12Z
== scw/qwen3-235b-a22b-instruct-2507
   small(16)   : HTTP 401 err='{"error":{"message":"[scaleway] All 1 connection(s) credits exhausted \ufffd please reconnect in the dashboard","type":"authentication_error","code":"invalid_api_key"}}'
   large(32768): HTTP 401 err='{"error":{"message":"[scaleway] All 1 connection(s) credits exhausted \ufffd please reconnect in the dashboard","type":"authentication_error","code":"invalid_api_key"}}'
```
Scaleway is credits-exhausted (operator reconnect pending), so the live 400-vs-200 leg proof is
blocked; 401 blocks both sizes identically. (Earlier run 14:54:31Z identical.) The leg is still tried
live: `t1-orchestrator` at 14:58:53Z logged `Trying model 2/5: scw/qwen3-235b-a22b-instruct-2507` →
`Provider scw auth failure (401)`.

### 3c. Replay pending one restart
The un-gated clamp only takes effect once the gateway reloads the patched files (the running pid has
the old bytes in memory). Expected after restart: a 32768 request whose target is the qwen leg is
**clamped to 16384 and forwarded** (then 401 until scaleway reconnects), instead of forwarded as 32768.

### 3d. Unrelated t2-worker small ack — unaffected
```
UTC 2026-09-30T15:05:10Z
== t2-worker max_tokens=2048  -> HTTP 200 model=zai-org/GLM-5.2 finish=stop content='ack-leg'
== t2-worker max_tokens=32768 -> HTTP 200 model=zai-org/GLM-5.2 finish=stop content='ack-leg'
```
(`t2-worker` now resolves to the newly added OVH/GLM legs — combos changed during the run, as the
mission warned. The earlier 14:58:42Z 16-token run served `gpt-oss-120b` with `finish=length` empty
content, i.e. a too-small budget for a reasoning model, not a regression.)

### 3e. Config read-back
`GET /api/model-capability-overrides` → the row in §1.4.

---

## 4. Reviewer leaf (t3-reviewer) — reconcile

- Finding (first draft): the patch edited `dist/open-sse/mcp-server/server.js`, which is the **MCP
  stdio** bundle (`bin/mcp-server.mjs`); the HTTP gateway is `dist/server-ws.mjs` → `dist/.build/next`.
  Verdict: **reject**.
- Reconciled and independently re-verified here: the gateway process command line is
  `node …\dist\server-ws.mjs`; `.build/next` has **4** copies of the clamp, now patched; `node --check`
  clean. The mcp bundle is still patched (harmless; it serves MCP clients).
- Reviewer's second finding (still open): direct `provider/model` calls never reach
  `executeModelUnit`/the clamp — see §5.

---

## 5. Open items / boundaries

1. **Direct `provider/model` calls are not clamped.** They bypass the combo path (measured, §1.4) and
   the clamp lives only in combo dispatch. The mission's named vehicle (`t2-worker`) *is* a combo and
   is fixed. Covering the direct path means a clamp at the direct route's body finalisation (a
   different, minified Next chunk), or — durably — an upstream OmniRoute source change to
   `open-sse/services/reasoningTokenBuffer.ts` + the direct handler. Recommend the upstream fix; hand
   that to L0 as a follow-up.
2. **Restart required, NOT executed by this lane** (would drop the live session/apply window).
   Command: stop pid 126780 (`Stop-Process -Id 126780`) — or the service that owns it — and relaunch
   the workstation stack (`configuration/start-stack.ps1`). Rollback: restore the five
   `*.autoos-backup-20260930-171528` files.
3. **Scaleway reconnect** (operator, pending) is required for the live 200 leg proof.
4. Vendor/generated-code patch: `npm install`/upgrade of omniroute overwrites `.build/next` and the mcp
   bundle — re-run `patch-gateway-clamp.ps1` after any upgrade (that is its purpose).

---

## 6. Results table

| # | Check | Result |
|---|---|---|
| a | clamp decision, instruct model, cap 16384, req 32768 | 16384 (unit-proven, before=null) |
| a | clamp decision, req 16 | 16 (unchanged) |
| a | gateway chunks carrying the gate | 4 → 0 (verified over 14213 files) |
| a | `node --check` patched files | exit 0 ×5 |
| b | replay 32768 direct | HTTP 401 credits-exhausted (verbatim §3b) |
| c | t2-worker 2048 / 32768 | HTTP 200 GLM-5.2 `ack-leg` |
| d | config read-back | cap row present, idempotent |
| e | reviewer | reject (draft target) → reconciled (§4) |
