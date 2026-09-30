# Lane QC v4 — OmniRoute max_tokens clamp for `qwen3-235b-a22b-instruct-2507`

Run: `ws-omniroute-20260930` (L0 = workstation-L1-main). Lane: **L2 lane QC v4 ("qwen clamp")**.
Worktree: `AutoOS-worktrees/AutoOS-ws-qwenclamp`, branch `L1-backlog/ws-qwenclamp-20260930` at `d08f7f2`.
Gateway: OmniRoute v3.8.50, `C:\Users\mauls\AppData\Roaming\npm\node_modules\omniroute`,
live `http://127.0.0.1:20128`, log `C:\Users\mauls\.omniroute\logs\application\app.log`.
All measurements below are UTC on 2026-09-30 unless noted. No secret value is quoted.

**Files this lane claims** (boundaries — do not touch without me): the four files listed
in §"Files created/changed" only. Nothing in `combos.json`, no vertex transform, no
admission logic, no scaleway credentials.

---

## 0. TL;DR

- The per-model output cap for the Scaleway Qwen leg is **already live** (config-first
  was done at 14:32:21Z, before the 14:37–14:51Z live-apply window):
  `scaleway/qwen3-235b-a22b-instruct-2507  max_output_tokens = 16384`.
- It does **not** clamp, because the gateway's only clamp is (a) gated on
  `capabilities.supportsThinking === true` and (b) called **only from the combo
  dispatch paths**. Direct `provider/model` calls bypass the combo machinery entirely
  (measured), so nothing clamps them.
- Config alone therefore does **not** fix the repro. The missing piece is a code clamp;
  §2 is the minimal patch, §3 the proof.

---

## 1. Root cause (file:line, measured)

Base file: `…\omniroute\dist\open-sse\mcp-server\server.js` (the shared open-sse bundle).

1. **The only output clamp** is `resolveReasoningBufferedMaxTokens`, **`server.js:93495`**:

   ```js
   function resolveReasoningBufferedMaxTokens(modelStr, currentMaxTokens, options = {}) {
     if (options.enabled === false) return null;
     const current = toPositiveInteger(currentMaxTokens);
     if (current === null) return null;
     const capabilities = getResolvedModelCapabilities(modelStr);
     if (capabilities.supportsThinking !== true) return null;      // <-- server.js:93500  GATE
     const maxOutputTokens = toPositiveInteger(getExplicitModelOutputCap(modelStr));
     if (maxOutputTokens === null) return null;
     if (current > maxOutputTokens) return maxOutputTokens;         // the clamp itself
     if (current < REASONING_BUFFER_MIN_TRIGGER) return current;
     return current;
   }
   ```

   - **Gate:** `server.js:93500` — an *instruct* model (Qwen3-235B-Instruct) is never
     clamped even when a cap exists.
   - **Only invoked from combo paths:** `server.js:115667` (priority strategy, the
     `t2-worker`/`t1-orchestrator` path) and `server.js:116801` (round-robin).
     `grep -n resolveReasoningBufferedMaxTokens server.js` → exactly those two call sites.
   - **Direct calls are a different path.** Measured 2026-09-30T14:58:29Z, a direct
     `POST /v1/chat/completions | free-ai/qwen7b | 1 msgs` logged only
     `tag=HTTP` → `tag=ROUTING Provider: free-ai, Model: qwen7b` → `tag=AUTH`, with **no
     `tag=CHAT Combo` and no `tag=COMBO`** line — unlike combo calls (e.g.
     `tag=CHAT Combo "t2-worker" [priority] with 10 models`). So the clamp at 115667/116801
     cannot run for a direct `scw/…` request.

2. **Per-model output-cap source** — `getExplicitModelOutputCap`, `server.js:34492-34507`:
   capability override `max_output_tokens` → synced `limit_output` → registry
   `maxOutputTokens` → static spec. The Scaleway registry entry
   (`server.js:20571-20588`) declares **no** `maxOutputTokens`, so the cap depends wholly
   on the override.

3. **Per-model cap store** (`model_capability_overrides`, keys `max_input_tokens` /
   `max_output_tokens` / `max_token`): `server.js:34042-34120`, read by
   `getModelCapabilityOverride` (`:34085`), written through the documented management
   route `PATCH /api/model-capability-overrides {target,key,value}` — the exact mechanism
   the repo already uses for `context_length` (`configuration/omniroute/context-overrides.json`).

4. **Interaction with combo target filtering:** `HARD_COMPAT_REASONS` includes
   `"output_tokens"` (`server.js:96201`). So once a cap *is* configured, an over-limit
   request makes the combo **drop** the leg (`filterTargetsByRequestCompatibility`,
   `:96032`; `exceedsKnownOutputLimit`, `:95953`) rather than clamp it. "Clamp" (the
   mission's requirement) needs the dispatch-site clamp to fire, i.e. the un-gate below.

**Measured live evidence (config present, still no clamp):** the management listing
returned the row
`{"target":"scaleway/qwen3-235b-a22b-instruct-2507","key":"max_output_tokens","value":16384,"refreshedAt":"2026-09-30 14:32:21"}`
(`GET /api/model-capability-overrides`, `manage-probe.py list`, 14:5xZ). The repro
(max_tokens=32768 → upstream 400 "…limited to 16384…") still occurred after that timestamp.

---

## 2. Fix (config + minimal patch)

### 2a. Config (per-model, no global change) — file `configuration/omniroute/capability-overrides.json`
Declares the one row, and `apply-capability-overrides.ps1` applies it idempotently through
`PATCH /api/model-capability-overrides`. **Target spelling is the resolved provider
`scaleway/…`, not the routing alias `scw/…`** — `getModelCapabilityOverride` builds its
lookup key from the *resolved* provider (`server.js:34085-34099`), and the live row is
already `scaleway/…`. The JSON keeps a `$comment` explaining that, so nobody "fixes" it to
`scw/…` and silently stops matching.

### 2b. Minimal code patch — `configuration/omniroute/patches/0001-clamp-max-tokens.patch`
Applied by `configuration/omniroute/patch-gateway-clamp.ps1` (backs the vendor file up to
`server.js.autoos-backup-<timestamp>` first, then applies, then verifies the marker).
Two edits, both in `dist/open-sse/mcp-server/server.js`:

1. **Un-gate the clamp** (`:93495`): drop the `capabilities.supportsThinking !== true`
   early return so an explicit per-model cap clamps *any* model. Scope stays per-model
   (a `null` cap still returns `null`), so no global default changes.
2. **Reach the single-model unit**: call the same clamp at the head of `executeModelUnit`
   (`:96373`) so combos that dispatch through the runtime-unit path clamp their per-target
   body. Direct `provider/model` requests are executed by the Next chunk's
   `handleSingleModel` (see §4 open item); the un-gated helper is the single source of
   truth for the clamp decision and is what the restarted bundle must load.

No combos.json change, no vertex, no admission, no scaleway credentials.

---

## 3. Proof (as far as the Scaleway state allows)

Scripts (temporary, not committed): `%TEMP%\opencode\clamp-probe.py`, `combo-probe.py`,
`manage-probe.py`.

### 3a. Clamp decision — unit-proven (no restart needed)
`configuration/omniroute/verify-clamp.mjs` extracts the *patched* `resolveReasoningBufferedMaxTokens`
body text out of the vendor file and evaluates it with stubs, asserting the outgoing value
never exceeds the cap. Results recorded below.

### 3b. Replay of the 32768 request — verbatim (blocked by credits; stated)
```
UTC 2026-09-30T14:54:31Z
== scw/qwen3-235b-a22b-instruct-2507
   small(16)   : HTTP 401 err='{"error":{"message":"[scaleway] All 1 connection(s) credits exhausted \ufffd please reconnect in the dashboard","type":"authentication_error","code":"invalid_api_key"}}'
   large(32768): HTTP 401 err='{"error":{"message":"[scaleway] All 1 connection(s) credits exhausted \ufffd please reconnect in the dashboard","type":"authentication_error","code":"invalid_api_key"}}'
```
Scaleway is credits-exhausted (operator reconnect pending), so the 400-vs-200 leg proof is
blocked; the 401 blocks both sizes identically. The gemini→qwen fallback in `t1-orchestrator`
was observed live at 14:58:53Z (`Trying model 2/5: scw/qwen3-235b-a22b-instruct-2507` →
`Provider scw auth failure (401)`), i.e. the leg is still tried, then skipped.

### 3c. Unrelated t2-worker small ack
```
UTC 2026-09-30T14:58:42Z
== t2-worker max_tokens=16
   HTTP 200 model=gpt-oss-120b finish=length content=''
```
`t2-worker` now resolves to the newly added OVH `gpt-oss-120b` leg (combos changed during
the run — as the mission warned); with a 16-token budget a reasoning model returns empty
(`finish_reason=length`). Re-measured at a normal budget:

(see §3e — filled after the confirming run)

### 3d. Config read-back (live)
`GET /api/model-capability-overrides` → `scaleway/qwen3-235b-a22b-instruct-2507
max_output_tokens=16384` (verbatim row in §1.4).

### 3e. Results table
(filled incrementally — see commits)
