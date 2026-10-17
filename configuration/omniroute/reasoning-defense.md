# DeepSeek Reasoning Defense — Configuration Note

**Date:** 2026-09-30  
**Scope:** `configuration/omniroute/` — documents the gateway's reasoning replay
defense for DeepSeek thinking-mode models, the root cause of the 400 error, and
the config-level mitigations available to operators.

## The 400 Error

```
400: The `reasoning_text` in the thinking mode must be passed back to the API.
```

DeepSeek's thinking-mode API requires that when an assistant message with
`tool_calls` is sent in the conversation history, it must include the
`reasoning_content` field with the *actual* reasoning text from the prior turn.
An empty string (`reasoning_content: ""`) is **not** sufficient — DeepSeek
rejects it with the 400 above.

## Gateway Defense Pipeline (v3.8.50)

The gateway has three defense layers, applied in order at `translator/index.ts`:

1. **Preserve Reasoning** (`openaiHelper.ts:69-78`, `preserveReasoningContent`)
   When `isReasoner` is true, the gateway does NOT strip `reasoning_content`
   from assistant messages in the *request*. This helps only when the client
   includes it — the AI SDK typically does **not**.

2. **Empty Reasoning Injection** (`schemaCoercion.ts:455-487`)
   Adds `reasoning_content: ""` to assistant messages with `tool_calls` that
   lack the field. Guarded by `!requiresExplicitReasoningReplay`
   (`translator/index.ts:614`). This is a **false safety net** — DeepSeek
   rejects empty reasoning when the original was non-empty.

3. **Reasoning Replay Cache** (`reasoningCache.ts`, `translator/index.ts:639`)
   Re-injects the *actual* cached reasoning, keyed by `tool_call_id`. This is
   the **primary defense**. Stored in both an in-memory map and the
   `reasoning_cache` SQLite table (TTL: 2 hours).

### When the 400 Fires

The 400 fires when the Replay Cache **misses** or when the `toResponses` function omits the reasoning item entirely:

| Scenario | Cache | Empty Injection | Result |
|---|---|---|---|
| Normal: tool_call_id preserved | HIT | fires (redundant) | 200 |
| tool_call_id overwritten/mismatched | MISS | fires (`""`) | **400** |
| Cache expired (>2h since tool call) | MISS | fires (`""`) | **400** |
| `interleaved_field="reasoning_content"` + cache miss | MISS | **skipped** | **400** |
| Cold start (DB cleared) | MISS | fires (`""`) | **400** |

The empty injection is insufficient because DeepSeek validates that the
`reasoning_content` matches what it returned in the prior turn. An empty
string passes only when the original reasoning was also empty.

## `requiresExplicitReasoningReplay` Logic

```
reasoningCache.ts:97   if (interleavedField === "reasoning_content") return true;
reasoningCache.ts:114  if (isDeepSeekReasoningModel(...)) return true;  // V4 pattern
reasoningCache.ts:116  if (!allowLegacyFallback) return false;
reasoningCache.ts:119  if (REASONING_REPLAY_PROVIDERS.has(provider)) return true;
```

- `requiresExplicitReasoningReplay` (allowLegacyFallback: false): true when
  `interleaved_field = "reasoning_content"` (models.dev sync) or model matches
  `/deepseek[-/]v4[-.](flash|pro)/i`. When true, the empty injection is SKIPPED.
- `isReasoner` (allowLegacyFallback: true): true when provider is `"deepseek"`
  (in `REASONING_REPLAY_PROVIDERS`). When true, the replay cache fires.

For the current combo `deepseek-v4.1-flash` → upstream `deepseek/deepseek-flash`:
- `interleaved_field` = NULL (model_capabilities table empty for DeepSeek)
- `requiresExplicitReasoningReplay` = false → empty injection fires (but insufficient)
- `isReasoner` = true → replay cache fires (primary defense)

## Config Mitigations

### 1. Model Path (opencode config)

> Superseded 2026-10-08 (AO-DENYLEGS D2 / operator D-657): no probe-d657 gateway
> acked `deepseek/deepseek-flash`, so its route renders no combo and apply.sh
> prunes `deepseek-v4.1-flash` from the store. The instruction below is kept as
> the measured reasoning analysis, not as a current model path — read
> `docs/models.md` for what serves.

Use `omniroute/deepseek-v4.1-flash` (combo through the gateway), NOT
`deepseek/deepseek-v4-flash` (no deepseek provider in opencode — unresolvable)
or `deepseek/deepseek-flash` (direct, bypasses combo context/override config).

The combo name `deepseek-v4.1-flash` (with `.1`) does NOT match the V4
pattern `/deepseek[-/]v4[-.](flash|pro)/i`, keeping `requiresExplicitReasoningReplay`
false and the empty injection active as a secondary defense.

### 2. Model Capabilities Sync

The `model_capabilities` table is currently empty for DeepSeek. If the
models.dev sync runs and sets `interleaved_field = "reasoning_content"`, the
empty injection is skipped, leaving only the replay cache. The
`model_capability_overrides` table does NOT support `interleaved_field` as a
key (only `max_input_tokens`, `max_output_tokens`, `max_token`,
`reasoning_efforts`), so there is no config-level override for this.

If the sync populates this field and the 400 becomes frequent, the workaround
is to clear the synced row:

```sql
DELETE FROM model_capabilities WHERE provider = 'deepseek' AND model_id = 'deepseek-flash';
```

### 3. Replay Cache TTL

The cache TTL is 2 hours (7200s), stored in `storage.sqlite`. This is sufficient
for typical agentic sessions (tool calls execute in seconds). Long-running
sessions with >2h gaps between tool call and round trip may see the 400.

## Combo Configuration

The combo `deepseek-v4.1-flash` is defined in `combos.json`:

```json
{
  "name": "deepseek-v4.1-flash",
  "strategy": "priority",
  "context": "1M",
  "models": ["deepseek/deepseek-flash"]
}
```

Context override for `deepseek/deepseek-flash` is in `context-overrides.json`
(1,048,576 tokens — the vendor's 1M window).

## Files

| File | Role |
|---|---|
| `combos.json` | Combo definitions (don't edit — P1-combos) |
| `context-overrides.json` | Model context window overrides |
| `apply.ps1` / `apply.sh` | Applies combos + overrides to the gateway |
| `reasoning-defense.md` | This file |
