# Lane FreeProbe — free-provider wiring evidence + proposal (2026-09-30)

**Lane:** `L1-backlog/ws-free-probe-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-free-probe`
**Branch base:** `origin/main` @ `e58274a`
**Probe script:** `tools/probe-free.py` (NEW)
**Raw log:** `logs/probe-free-20260930.jsonl` (git-ignored — 43 leg records + 8 backoff lines)
**Gateway:** `http://127.0.0.1:20128` (live; `/api/health` → `{"status":"ok"}` @ 2026-09-30T20:32:41Z)
**Gateway package:** `omniroute` **v3.8.50** (`%APPDATA%\npm\node_modules\omniroute\package.json`)
**Timestamp of measurement:** 2026-09-30T20:34:04Z → 21:13:23Z (UTC)

This lane is **read-only on routing config**. It does **not** edit
`configuration/omniroute/combos.json` or `catalog/ai-registry.json`. Everything
in §4–§7 is a proposal for the single-writer wiring lane.

---

## 1. Method

Each candidate leg is addressed exactly as the gateway's OpenAI-compatible
surface expects: the JSON `"model"` field is the **plain leg string**
(`<provider-prefix>/<model>`, e.g. `groq/openai/gpt-oss-120b`), never an
`omniroute/` prefix. Per leg, two calls:

1. **ack** — `max_tokens: 16`, prompt `Reply with exactly: ACK_OK`. Records HTTP
   status and latency.
2. **tool** — one offered `get_weather(city)` function, `max_tokens: 1024`.
   Pass = **exactly one** `tool_call` named `get_weather` whose arguments parse
   as JSON with a `city` containing `paris` (the same shape
   `tools/probe-toolcalls.py` uses).

The client key is read in-memory from `AUTOOS_OMNIROUTE_KEY` or the checkouts'
`configuration/api-keys.yml` (`tools/autoos-agent.py:client_key`) and is never
printed, logged or written. 429/503/504 are retried with 60 s then 120 s backoff
(logged UTC, §8).

**Reading the `ack` column.** Several reasoning models (gpt-oss, GLM, Gemini)
spend the 16-token budget on *reasoning* tokens and return empty `content`
(e.g. groq `gpt-oss-120b` used 14/16 reasoning tokens). The `ack` content check
therefore reports `fail` for those legs while the HTTP call itself is `200`. The
**capability verdict is the tool call** (1024 tokens); the ack column is kept to
show the leg accepted and answered. This document treats **`tool = ok`** as
"proven".

Provider tier / policy comes from `catalog/ai-registry.json`:
`providers.*.tier`, `models.*.tier`, and `policy.leg_rules`. Context/output
limits are read from the live gateway `/v1/models` row (authoritative), by
`tools/gw-leg-meta.py`.

---

## 2. (a) Leg × result matrix (measured 2026-09-30)

Context/output are the gateway `/v1/models` values. `tier` is the registry's
effective tier. `gate` is `policy.leg_rules` (`—` = no matching rule).

| # | Leg | tier | gate | ack HTTP | ack ms | tool | tool ms | calls | ctx | out | verbatim error (bounded) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `groq/openai/gpt-oss-120b` | free | **deny-groq** | 200 | 412 | **ok** | 204 | 1 | 131072 | — | — |
| 2 | `groq/openai/gpt-oss-20b` | free | **deny-groq** | 200 | 534 | **ok** | 301 | 1 | 131072 | — | — |
| 3 | `groq/qwen/qwen3.8-27b` | free | **deny-groq** | 200 | 211 | **ok** | 420 | 1 | 131072 | — | — |
| 4 | `cerebras/gpt-oss-120b` | free | — | 401 | 7 | fail | 5 | 0 | — | — | `No active credentials for provider: cerebras.` |
| 5 | `cerebras/qwen-3.8-27b` | free | — | 401 | 5 | fail | 5 | 0 | — | — | `No active credentials for provider: cerebras.` |
| 6 | `cloudflare-ai/@cf/moonshotai/kimi-k2.6` | free | — | 502 | 11947 | fail | 11270 | 0 | 262144 | — | `Cloudflare Workers AI requires an Account ID. Add it in provider settings under 'Account ID'.` |
| 7 | `cloudflare-ai/@cf/zai-org/glm-4.7-flash` | free | — | 502 | 11933 | fail | 11721 | 0 | — | — | same Account-ID message |
| 8 | `cloudflare-ai/@cf/qwen/qwq-32b` | free | — | 502 | 11880 | fail | 11843 | 0 | — | — | same Account-ID message |
| 9 | `cloudflare-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast` | free | — | 502 | 11921 | fail | 11576 | 0 | — | — | same Account-ID message |
| 10 | `sambanova/gpt-oss-120b` | free | — | 401 | 3509 | fail | 5 | 0 | 400000 | — | `[401]: Incorrect API key provided: &lt;masked-key-prefix&gt;.` |
| 11 | `sambanova/DeepSeek-V3.2` | free | — | 401 | 7 | fail | 6 | 0 | — | — | `[sambanova] All 1 connection(s) authentication expired` |
| 12 | `sambanova/MiniMax-M2.7` | free | — | 401 | 5 | fail | 5 | 0 | — | — | `[sambanova] All 1 connection(s) authentication expired` |
| 13 | `huggingface/zai-org/GLM-5.2` | free | — | 200 | 835 | **ok** | 1132 | 1 | 262144 | — | — |
| 14 | `huggingface/deepseek-ai/DeepSeek-V4-Flash-0731` | free | — | 200 | 726 | **ok** | 1635 | 1 | 128000 | — | — |
| 15 | `huggingface/Qwen/Qwen3.8-27B` | free | — | 200 | 1073 | **ok** | 1276 | 1 | 128000 | — | — |
| 16 | `together/zai-org/GLM-5.2` | credit | — | 403 | 3073 | fail | 3216 | 0 | — | — | Cloudflare **Error 1010** `browser_signature_banned` (zone `api.together.xyz`), `owner_action_required:true`, `retryable:false` |
| 17 | `together/Qwen/Qwen3.8-Flash` | credit | — | 403 | 3100 | fail | 3070 | 0 | — | — | same Error 1010 (zone `api.together.xyz`) |
| 18 | `novita/zai-org/glm-5.2` | free | — | 403 | 33308 | fail | 27074 | 0 | — | — | Cloudflare **Error 1010** (zone `api.novita.ai`), `**Do not retry.**` |
| 19 | `novita/deepseek/deepseek-v4.1-flash` | free | — | 403 | 21211 | fail | 24080 | 0 | — | — | `Model deepseek/deepseek-v4.1-flash forbidden (per-model access/subscription)` (403) |
| 20 | `navy/glm-5.2` | free | — | 403 | 33249 | fail | 27099 | 0 | 1000000 | 131072 | Cloudflare **Error 1010** (zone `api.navy`), `owner_action_required:true`, `retryable:false` |
| 21 | `navy/deepseek-v4.1-flash` | free | — | 403 | 21277 | fail | 24061 | 0 | 128000 | — | Cloudflare **Error 1010** (zone `api.navy`) |
| 22 | `navy/gpt-oss-120b` | free | — | 403 | 3353 | fail | 42282 | 0 | 400000 | — | Cloudflare **Error 1010** (zone `api.navy`) |
| 23 | `bzl/glm-5.2` | free | — | 400 | 7 | fail | 5 | 0 | — | — | `Model 'glm-5.2' is not available in the active live catalog for provider 'bazaarlink'.` |
| 24 | `bzl/deepseek-v4.1-flash` | free | — | 402 | 336 | fail | 8 | 0 | 1000000 | — | `[402]: Insufficient credits. Please top up to continue.` |
| 25 | `bzl/deepseek/deepseek-v4-flash-0731free:free` | free | — | 401 | 8 | fail | 6 | 0 | 1048576 | — | `[bazaarlink] All 1 connection(s) credits exhausted` |
| 26 | `bm/glm-4.7` | free | — | 400 | 5 | fail | 5 | 0 | 128000 | — | `Model 'glm-4.7' is not available in the active live catalog for provider 'bluesminds'.` |
| 27 | `bm/deepseek-chat` | free | — | 400 | 6 | fail | 5 | 0 | 128000 | — | `Model 'deepseek-chat' is not available in the active live catalog for provider 'bluesminds'.` |
| 28 | `bm/gpt-oss-20b` | free | — | 504 | 122712 | fail | 122847 | 0 | 128000 | — | `[504]: Direct response did not start within 30000ms - retrying on a fresh socket` |
| 29 | `agentrouter/deepseek-v4-flash` | free | — | 200 | 1441 | fail | 1330 | 0 | 1000000 | 384000 | tool 400: `Failed to deserialize ... tools[0]: unknown variant 'custom', expected 'web_search_20250305' or 'web_search_20260209'` |
| 30 | `cheaperinference/glm-5.2` | **paid** | allow | 200 | 4158 | **ok** | 3748 | 1 | 1000000 | 131072 | — |
| 31 | `cheaperinference/kimi-k3` | **paid** | allow | 200 | 4346 | **ok** | 4054 | 1 | 1048576 | 1048576 | — |
| 32 | `cheaperinference/deepseek-v4-flash` | **paid** | allow | 200 | 5238 | **ok** | 4122 | 1 | 1000000 | 384000 | — |
| 33 | `openrouter/z-ai/glm-5.2:free` | paid | **deny-openrouter** | 400 | 16 | fail | 5 | 0 | 32768 | — | `Model 'z-ai/glm-5.2:free' is not available in the active live catalog for provider 'openrouter'.` |
| 34 | `openrouter/qwen/qwen3.8-27b:free` | paid | **deny-openrouter** | 200 | 20993 | **ok** | 5842 | 1 | 262144 | 235929 | — |
| 35 | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | paid | **deny-openrouter** | 200 | 675 | **ok** | 801 | 1 | 262144 | 235929 | — |
| 36 | `openrouter/google/gemma-4-31b-it:free` | paid | **deny-openrouter** | 200 | 28758 | fail | 66758 | 0 | 262144 | 32768 | tool 502: `upstream returned an empty response without usable output` |
| 37 | `openrouter/thinkingmachines/inkling:free` | paid | **deny-openrouter** | 403 | 24425 | fail | 24070 | 0 | — | — | `[403]: thinkingmachines/inkling:free is only available on agentic harnesses.` |
| 38 | `openrouter/cohere/north-mini-code:free` | paid | **deny-openrouter** | 200 | 654 | **ok** | 809 | 1 | 256000 | 64000 | — |
| 39 | `openrouter/poolside/laguna-s-2.1:free` | paid | **deny-openrouter** | 200 | 637 | **ok** | 1961 | 1 | 262144 | 32768 | — |
| 40 | `opencode-zen/deepseek-v4.1-flash` | paid | allow (client-bound) | 402 | 673 | fail | 32 | 0 | — | — | `[402]: This model requires an opencode API key - add one in Settings → Providers.` |
| 41 | `opencode/deepseek-v4.1-flash` | free | — | 402 | 6 | fail | 6 | 0 | — | — | same 402 (vendor: outside OpenCode) |
| 42 | `antigravity/claude-opus-4-6-thinking` | free | allow | 401 | 6 | fail | 5 | 0 | — | — | `[antigravity] All 1 connection(s) authentication expired` |
| 43 | `devin/devin` | subscription | — | 502 | 25999 | fail | 16767 | 0 | — | — | `[devin/devin] Devin ACP error -32603: ... Authentication required: invalid api key` |

**Proven (tool = ok):** 3 groq, 3 huggingface, 3 cheaperinference, 4 OpenRouter
`:free`, plus `vertex/gemini-3.8-flash` (probed separately, §5c).
**Ack-only / not proven:** `agentrouter/deepseek-v4-flash` (endpoint rejects the
OpenAI function-call shape).
**13 legs proven (tool = ok)**, 1 leg ack-only (`agentrouter/deepseek-v4-flash`,
endpoint rejects the OpenAI function-call shape), **29 legs failed at the
credential / config / transport / vendor-ban layer** (401/402/403/502-config/504).

### 2.1 Gateway connection state (read-only `/api/providers`, same window)

Quoted fields only (no key, no account name — AGENTS.md §1.1):

| provider | testStatus | errorCode | lastErrorType | lastError (bounded) |
|---|---|---|---|---|
| `novita` | active | 403.0 | forbidden | `Model deepseek/deepseek-v4.1-flash forbidden (per-model access/subscription)` |
| `cloudflare-ai` | active | — | — | — |
| `together` | active | 403.0 | fingerprint_rejection | Cloudflare 1010 browser_signature_banned |
| `sambanova` | **expired** | 401.0 | unauthorized | `[401]: Incorrect API key provided: &lt;masked-key-prefix&gt;.` |
| `navy` | active | — | — | — (403 is upstream Cloudflare 1010, not a gateway auth error) |
| `bluesminds` | active | — | — | — |
| `cheaperinference` | active | — | — | — (402 cleared since 2026-09-27) |
| `agentrouter` | active | — | — | — |
| `bazaarlink` | active | — | — | — (legs 402/401 credits exhausted) |
| `cerebras` | active | — | — | `isActive: false` |
| `arcee-ai` | unknown | missing_api_key | auth_missing | (empty — no key configured) |
| `antigravity` | **expired** | unrecoverable_refresh_error | unrecoverable_refresh_error | `Refresh token rejected (unrecoverable_refresh_error). Please re-authenticate this account.` |
| `claude` | **expired** | no_refresh_token | no_refresh_token | `No refresh token available — re-authenticate this account.` |
| `scaleway` | credits_exhausted | 429.0 | rate_limited | `[429]: You exceeded your current quota of tokens per minute.` |

---

## 3. Proven legs and their policy gate

The gateway proves capability; the registry decides eligibility. Two proven
free bands are currently **policy-denied**:

| Proven leg | provider tier | registry gate | can be wired today? |
|---|---|---|---|
| `huggingface/zai-org/GLM-5.2` | free | none | **yes** |
| `huggingface/deepseek-ai/DeepSeek-V4-Flash-0731` | free | none | **yes** (name = V4, not V4.1 — see caveat) |
| `huggingface/Qwen/Qwen3.8-27B` | free | none | **yes** (new models row needed) |
| `groq/qwen/qwen3.8-27b`, `groq/openai/gpt-oss-120b`, `groq/openai/gpt-oss-20b` | free | `deny-groq` (`groq/*` → allow:false) | **only after an operator decides to lift `deny-groq`** |
| `openrouter/qwen/qwen3.8-27b:free`, `openrouter/nvidia/nemotron-3-super-120b-a12b:free`, `openrouter/cohere/north-mini-code:free`, `openrouter/poolside/laguna-s-2.1:free` | paid (registry) | `deny-openrouter` (`openrouter/*` → allow:false) | **only after per-leg `allow` rules are added above `deny-openrouter`** |
| `cheaperinference/glm-5.2`, `cheaperinference/kimi-k3`, `cheaperinference/deepseek-v4-flash` | **paid** | allow (glm-5.2, kimi-k3) / **deny** (deepseek-v4-flash) | paid credit band, **not free** — no free-band insertion |

**Caveats the wiring lane must carry:**

- **HF `DeepSeek-V4-Flash-0731`** is **V4**, not "V4.1". The operator rule is
  *"DeepSeek only V4.1 Flash"* (`deny-deepseek`, and the
  `deepseek-v4.1-flash` route `$comment`). On its face this leg violates that
  rule; it is listed here as a proven-capability fact, **not** as a wiring
  recommendation. Wiring it needs an explicit operator exception.
- **`deny-groq`** exists because groq failed every multi-turn agent call
  (400 `messages.N role:assistant` ×335, 413 too-large ×78 on the L0
  call_logs since 2026-09-26). A single `get_weather` round is *not* that
  workload: this probe proves the leg answers one tool call, and does **not**
  overturn the 400/413 history. Treat `groq/*` as "re-probe with a real
  multi-turn agent shape before lifting `deny-groq`".
- **`deny-openrouter`** exists because OpenRouter has no credit. The `:free`
  ids are genuinely $0 at the vendor; the registry's `openrouter` provider
  carries `tier: paid`, so a `:free` leg's `models.*.tier` must be set `"free"`
  for `*-free-only` eligibility and for honest pricing.

---

## 4. (b) Exact wiring proposal — registry first, combos.json is rendered

**Single source of truth.** `configuration/omniroute/combos.json` is *rendered*
from `catalog/ai-registry.json` (`tools/registry.py render omniroute`), and
`render omniroute --check` is a CI drift gate. The wiring lane edits the
**registry** (`routes.*.legs`, `models.*`, `providers.*`, `policy.leg_rules`)
and then regenerates `combos.json`. The combos.json JSON below is what the
render produces — do not hand-edit it.

### 4.1 New per-model rows (only for legs lacking one)

`resolve_leg()` splits at the **first** `/`, so the key is the part after
`huggingface/` / `openrouter/`:

```jsonc
// models — every schema-required key (catalog/ai-registry.schema.json /$defs/model
// requires id, family, context_advertised, context_usable, output_max, reasoning,
// effort_ladder, tool_calls, price_in, price_out) is present.
"Qwen/Qwen3.8-27B": {
  "id": "Qwen/Qwen3.8-27B", "family": "qwen", "context_advertised": 131072,
  "context_usable": {"source": "default", "tokens": 65536},
  "output_max": 32768, "reasoning": false, "effort_ladder": [], "tool_calls": "proven",
  "price_in": 0.0, "price_out": 0.0, "tier": "free", "trains_on_prompts": false,
  "$comment": "FREEPROBE 2026-09-30: huggingface free; tool-call proven (1132..1276 ms). trains_on_prompts follows providers.hugging_face (false)."
},
"qwen/qwen3.8-27b:free": {
  "id": "qwen/qwen3.8-27b:free", "family": "qwen", "context_advertised": 262144,
  "context_usable": {"source": "default", "tokens": 131072},
  "output_max": 235929, "reasoning": false, "effort_ladder": [], "tool_calls": "proven",
  "price_in": 0.0, "price_out": 0.0, "tier": "free", "trains_on_prompts": false,
  "$comment": "FREEPROBE 2026-09-30: OpenRouter :free id, $0 at vendor. tier:free is a MODEL-level override so *-free-only may carry it (registry.py private_safe())."
},
"nvidia/nemotron-3-super-120b-a12b:free": {
  "id": "nvidia/nemotron-3-super-120b-a12b:free", "family": "nvidia",
  "context_advertised": 262144, "context_usable": {"source": "default", "tokens": 131072},
  "output_max": 235929, "reasoning": true, "effort_ladder": [], "tool_calls": "proven",
  "price_in": 0.0, "price_out": 0.0, "tier": "free", "trains_on_prompts": false,
  "$comment": "FREEPROBE 2026-09-30: OpenRouter :free, tool-call proven (801 ms)."
},
"cohere/north-mini-code:free": {
  "id": "cohere/north-mini-code:free", "family": "cohere",
  "context_advertised": 256000, "context_usable": {"source": "default", "tokens": 128000},
  "output_max": 64000, "reasoning": false, "effort_ladder": [], "tool_calls": "proven",
  "price_in": 0.0, "price_out": 0.0, "tier": "free", "trains_on_prompts": false,
  "$comment": "FREEPROBE 2026-09-30: OpenRouter :free, tool-call proven (809 ms)."
},
"poolside/laguna-s-2.1:free": {
  "id": "poolside/laguna-s-2.1:free", "family": "poolside",
  "context_advertised": 262144, "context_usable": {"source": "default", "tokens": 131072},
  "output_max": 32768, "reasoning": false, "effort_ladder": [], "tool_calls": "proven",
  "price_in": 0.0, "price_out": 0.0, "tier": "free", "trains_on_prompts": false,
  "$comment": "FREEPROBE 2026-09-30: OpenRouter :free, tool-call proven (1961 ms)."
}
```

`huggingface/zai-org/GLM-5.2` and `huggingface/deepseek-ai/DeepSeek-V4-Flash-0731`
already have model rows (`zai-org/GLM-5.2`, `deepseek-ai/DeepSeek-V4-Flash-0731`).

### 4.2 New policy rules (required before any groq / openrouter leg renders)

`policy.leg_rules` is **ordered, first match wins**; an `allow` must sit
**above** the provider deny it overrides. Proposed insertions, placed directly
above the existing `deny-groq` and `deny-openrouter` rows:

```jsonc
{ "id": "allow-groq-qwen3.8-27b", "match": "groq/qwen/qwen3.8-27b", "allow": true,
  "reason": "FREEPROBE 2026-09-30: one get_weather tool call answered (420 ms). This does NOT overturn deny-groq's multi-turn 400/413 history; re-probe an agent shape first.",
  "source": "L1-backlog/ws-free-probe-20260930 2026-09-30" },
{ "id": "allow-groq-gpt-oss", "match": "groq/openai/gpt-oss-*", "allow": true,
  "reason": "FREEPROBE 2026-09-30: gpt-oss-120b/20b tool-call proven (204/301 ms). See allow-groq-qwen3.8-27b caveat.",
  "source": "L1-backlog/ws-free-probe-20260930 2026-09-30" },
{ "id": "allow-openrouter-free", "match": "openrouter/*:free", "allow": true,
  "reason": "FREEPROBE 2026-09-30: the :free ids are $0 at OpenRouter; 4/7 tool-call proven. deny-openrouter still covers every paid openrouter leg.",
  "source": "L1-backlog/ws-free-probe-20260930 2026-09-30" }
```

> A wildcard like `openrouter/*:free` is a proposal only — if the wiring lane
> prefers per-leg control, add four exact `allow` rows instead. **Never** put an
> `openrouter/*` bare allow above `deny-openrouter`.

### 4.3 New single-provider combos (registry routes → rendered combos.json)

For each proven free leg that passes its gate, one pinned route. Registry entry
shape (`surfaces.omniroute` carries `context_declared`; the render clamps the
combo context to the servable legs):

```jsonc
// routes — new entries (class "free")
"hf-glm-5.2": {
  "class": "free", "id": "hf-glm-5.2",
  "legs": ["huggingface/zai-org/GLM-5.2"],
  "strategy": "priority",
  "surfaces": { "omniroute": {
    "clients": ["opencode","zed","openhands"], "context": 262144,
    "context_declared": "256k", "display_name": "GLM-5.2 via HuggingFace (free)",
    "openhands_profile": {"max_input_tokens": 262144, "max_output_tokens": 32768, "reasoning": true},
    "output": 32768 } }
},
"hf-qwen3.8-27b": {
  "class": "free", "id": "hf-qwen3.8-27b",
  "legs": ["huggingface/Qwen/Qwen3.8-27B"],
  "strategy": "priority",
  "surfaces": { "omniroute": {
    "clients": ["opencode","zed","openhands"], "context": 131072,
    "context_declared": "128k", "display_name": "Qwen3.8-27B via HuggingFace (free)",
    "openhands_profile": {"max_input_tokens": 131072, "max_output_tokens": 32768, "reasoning": false},
    "output": 32768 } }
},
"or-nemotron-3-super-free": {
  "class": "free", "id": "or-nemotron-3-super-free",
  "legs": ["openrouter/nvidia/nemotron-3-super-120b-a12b:free"],
  "strategy": "priority",
  "surfaces": { "omniroute": {
    "clients": ["opencode","zed","openhands"], "context": 262144,
    "context_declared": "256k", "display_name": "Nemotron-3-Super-120B via OpenRouter :free",
    "openhands_profile": {"max_input_tokens": 262144, "max_output_tokens": 235929, "reasoning": true},
    "output": 235929 } }
},
"or-laguna-s-2.1-free": { "class": "free", "id": "or-laguna-s-2.1-free",
  "legs": ["openrouter/poolside/laguna-s-2.1:free"], "strategy": "priority",
  "surfaces": { "omniroute": { "clients": ["opencode","zed","openhands"],
    "context": 262144, "context_declared": "256k",
    "display_name": "Laguna-S-2.1 via OpenRouter :free",
    "openhands_profile": {"max_input_tokens": 262144, "max_output_tokens": 32768, "reasoning": false},
    "output": 32768 } } },
"or-north-mini-code-free": { "class": "free", "id": "or-north-mini-code-free",
  "legs": ["openrouter/cohere/north-mini-code:free"], "strategy": "priority",
  "surfaces": { "omniroute": { "clients": ["opencode","zed","openhands"],
    "context": 256000, "context_declared": "256k",
    "display_name": "North-Mini-Code via OpenRouter :free",
    "openhands_profile": {"max_input_tokens": 256000, "max_output_tokens": 64000, "reasoning": false},
    "output": 64000 } } },
"or-qwen3.8-27b-free": { "class": "free", "id": "or-qwen3.8-27b-free",
  "legs": ["openrouter/qwen/qwen3.8-27b:free"], "strategy": "priority",
  "surfaces": { "omniroute": { "clients": ["opencode","zed","openhands"],
    "context": 262144, "context_declared": "256k",
    "display_name": "Qwen3.8-27B via OpenRouter :free",
    "openhands_profile": {"max_input_tokens": 262144, "max_output_tokens": 235929, "reasoning": false},
    "output": 235929 } } },
"groq-qwen3.8-27b": { "class": "free", "id": "groq-qwen3.8-27b",
  "legs": ["groq/qwen/qwen3.8-27b"], "strategy": "priority",
  "surfaces": { "omniroute": { "clients": ["opencode","zed","openhands"],
    "context": 131072, "context_declared": "128k",
    "display_name": "Qwen3.8-27B via Groq (free)",
    "openhands_profile": {"max_input_tokens": 131072, "max_output_tokens": 32768, "reasoning": false},
    "output": 32768 } } }
```

Rendered `combos.json` entries (what `render omniroute` emits; provider
`model_prefix` is null for huggingface/openrouter/groq, so legs are unchanged):

```jsonc
{ "name": "hf-glm-5.2",              "strategy": "priority", "context": "256k", "models": ["huggingface/zai-org/GLM-5.2"] },
{ "name": "hf-qwen3.8-27b",          "strategy": "priority", "context": "128k", "models": ["huggingface/Qwen/Qwen3.8-27B"] },
{ "name": "or-nemotron-3-super-free","strategy": "priority", "context": "256k", "models": ["openrouter/nvidia/nemotron-3-super-120b-a12b:free"] },
{ "name": "or-laguna-s-2.1-free",    "strategy": "priority", "context": "256k", "models": ["openrouter/poolside/laguna-s-2.1:free"] },
{ "name": "or-north-mini-code-free", "strategy": "priority", "context": "256k", "models": ["openrouter/cohere/north-mini-code:free"] },
{ "name": "or-qwen3.8-27b-free",     "strategy": "priority", "context": "256k", "models": ["openrouter/qwen/qwen3.8-27b:free"] },
{ "name": "groq-qwen3.8-27b",        "strategy": "priority", "context": "128k", "models": ["groq/qwen/qwen3.8-27b"] }
```

Names avoid collision with the existing `gemini-3.8-flash` / `deepseek-v4.1-flash`
per-model groups. `hf-deepseek-v4-flash-0731` is deliberately **omitted** (V4 ≠
V4.1).

> **Render-gate caveat (reviewer, verified).** `render_ide` raises unless
> `IDE_MODEL_ORDER` (`tools/registry.py:1305`) matches the registry's routes
> exactly, and `render_openhands` raises unless `OPENHANDS_TIER_ORDER`
> (`tools/registry.py:1529`) matches every route with an `openhands_profile`.
> Adding the seven routes above therefore requires either (a) adding each id to
> those two tuples, or (b) omitting `"openhands"` from `omniroute.clients` **and**
> dropping `openhands_profile` (the `cheaperinference/kimi-k3` route at
> `catalog/ai-registry.json:3149` is the precedent for a pinned single-model
> route that does neither). Pick (b) unless the wiring lane also wants these
> tiers visible to OpenHands.

### 4.4 Free-band insertion — `t2-worker` and `t3-driver`

"Free band" = the leading run of `tier: free` legs, ahead of the paid/credit
band. Insert the proven legs into that run, keeping the FREEKEYS-2 rule
(adjacent legs on distinct providers). **`*-clean` is never touched.**
**`*-free-only` stays free-only** (only `tier: free` legs; that is why the
OpenRouter `:free` rows must carry `tier: "free"` at the *model* level).

**`t2-worker`** — registry `routes.t2-worker.legs` (gateway spelling in the
render is identical). Insert after `nebius/zai-org/GLM-5.2` (the last
unconditionally-usable free leg today), before `groq/openai/gpt-oss-120b`:

```jsonc
// inserted block — interleaved so no two adjacent legs share a provider
// (FREEKEYS-2 rule). Lines marked conditional need the §4.2 allow rules.
"openrouter/nvidia/nemotron-3-super-120b-a12b:free",   // conditional
"huggingface/zai-org/GLM-5.2",
"groq/qwen/qwen3.8-27b",                               // conditional
"openrouter/poolside/laguna-s-2.1:free",               // conditional
"huggingface/Qwen/Qwen3.8-27B",
"groq/openai/gpt-oss-20b",                             // conditional
"openrouter/cohere/north-mini-code:free",              // conditional
"groq/openai/gpt-oss-120b",                            // conditional (moved up from leg 6; dedupe)
"openrouter/qwen/qwen3.8-27b:free",                    // conditional
// NOT inserted: huggingface/deepseek-ai/DeepSeek-V4-Flash-0731 is V4, not V4.1
```

Resulting free band (registry legs 1..N, then the unchanged paid/credit tail):

```jsonc
"models": [
    "antigravity/gemini-3.7-flash-high",
    "scaleway/qwen3-235b-a22b-instruct-2507",
    "scaleway/mistral-small-3.2-24b-instruct-2506",
    "nebius/zai-org/GLM-5.2",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "huggingface/zai-org/GLM-5.2",
    "groq/qwen/qwen3.8-27b",
    "openrouter/poolside/laguna-s-2.1:free",
    "huggingface/Qwen/Qwen3.8-27B",
    "groq/openai/gpt-oss-20b",
    "openrouter/cohere/north-mini-code:free",
    "groq/openai/gpt-oss-120b",
    "openrouter/qwen/qwen3.8-27b:free",
    "cerebras/gpt-oss-120b",
    "sambanova/gpt-oss-120b",
    "openrouter/openai/gpt-oss-120b",
    "cheaperinference/deepseek-v4-flash",
    "cheaperinference/glm-4.5-air",
    "cheaperinference/kimi-k3",
    "openrouter/deepseek/deepseek-v4.1-flash",
    "deepseek/deepseek-flash",
    "opencode-zen/deepseek-v4.1-flash",
    "meta_api/muse-spark-1.3-contributor",
    "free_ai/qwen7b",
    "morph/morph-dsv4flash",
    "deepinfra/google/gemini-3.1-flash-lite"
]
```

(the `gemini/gemini-3.8-flash` head is removed per §5; the interleaved
HF/OpenRouter/groq block is the new free band). The existing `cerebras/*`,
`sambanova/*` legs stay where they are and remain gated by `unavailable_legs`
until their credential issues are fixed; `groq/openai/gpt-oss-120b` is deduped
into the new block if `deny-groq` is lifted.

**`t3-driver`** — insert the same block at the end of its free run
(after `scaleway/qwen3-235b-a22b-instruct-2507`), before the first paid leg
`mistral/mistral-code-latest`:

```jsonc
"models": [
    "scaleway/mistral-small-3.2-24b-instruct-2506",
    "nebius/zai-org/GLM-5.2",
    "scaleway/qwen3-235b-a22b-instruct-2507",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "huggingface/zai-org/GLM-5.2",
    "groq/qwen/qwen3.8-27b",
    "openrouter/poolside/laguna-s-2.1:free",
    "huggingface/Qwen/Qwen3.8-27B",
    "groq/openai/gpt-oss-20b",
    "openrouter/cohere/north-mini-code:free",
    "groq/openai/gpt-oss-120b",
    "openrouter/qwen/qwen3.8-27b:free",
    "mistral/mistral-code-latest",
    "samba/gpt-oss-120b",
    "cheaperinference/glm-5.2",
    "deepseek/deepseek-flash",
    "cheaperinference/kimi-k3",
    "samba/MiniMax-M3",
    "cerebras/qwen-3.8-27b",
    "cheaperinference/glm-4.5-air",
    "cheaperinference/minimax-m2.7",
    "opencode-zen/deepseek-v4.1-flash",
    "meta_api/muse-spark-1.3-contributor",
    "morph/morph-glm52-744b",
    "deepinfra/google/gemini-3.7-flash"
]
```

(Deduped: `t3-driver`'s pre-existing `groq/qwen/qwen3.8-27b` leg at position 5 is
folded into the new interleaved block; its `unavailable_legs` entry stays until
`deny-groq` is lifted. `cerebras/qwen-3.8-27b` and `samba/*` stay in the paid
tail unchanged.)

**`*-free-only` (kept free-only):** replacing the removed gemini head and
filling the dead groq/cerebras slots with the proven **free** legs:

```jsonc
// t2-worker-free-only (interleaved, free legs only)
"models": ["antigravity/gemini-3.7-flash-medium",
           "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
           "huggingface/zai-org/GLM-5.2",
           "groq/qwen/qwen3.8-27b",
           "openrouter/poolside/laguna-s-2.1:free",
           "huggingface/Qwen/Qwen3.8-27B",
           "scaleway/qwen3-235b-a22b-instruct-2507",
           "nebius/zai-org/GLM-5.2",
           "scaleway/mistral-small-3.2-24b-instruct-2506",
           "openrouter/qwen/qwen3.8-27b:free",
           "free_ai/qwen7b"]
// t3-driver-free-only (interleaved, free legs only)
"models": ["huggingface/zai-org/GLM-5.2",
           "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
           "groq/qwen/qwen3.8-27b",
           "huggingface/Qwen/Qwen3.8-27B",
           "openrouter/poolside/laguna-s-2.1:free",
           "scaleway/mistral-small-3.2-24b-instruct-2506",
           "nebius/zai-org/GLM-5.3-Flash",
           "scaleway/qwen3-235b-a22b-instruct-2507",
           "free_ai/qwen7b"]
```

**Never touched:** `t2-worker-clean`, `t3-driver-clean` (pinned `deepseek/deepseek-flash`),
`t1-orchestrator-clean`, all `*-paid`, `auto*`.

---

## 5. (c) Gemini removal plan

### 5.1 Health evidence (measured 2026-09-30, `~/.omniroute/logs/application/app.log`)

Analysed by `tools/analyse-gemini-log.py` / `tools/analyse-gemini-codes.py`
(filtered: count + error code only, no body values):

| metric (2026-09-30, whole day to 22:40 local) | count |
|---|---|
| `gemini` cooling-down lines | **8088** (operator reported ~7242 — same order; the log was still growing) |
| of those, `lastErrorCode=429.0` | **7187** (operator reported ~6766) |
| of those, `lastError=Model gemini-3.8-flash rate_limited` | 7136 |
| `403.0` (per-model forbidden) / `503.0` | 17 / 1 |
| all-provider cooling-down lines | 8218 → gemini is **98.4%** |
| live probe `gemini/gemini-3.8-flash` | HTTP **429** |

Verbatim live 429 (2026-09-30T21:13Z):

```
{"error":{"message":"All credentials for model gemini-3.8-flash are cooling down",
"type":"rate_limit_error","code":"model_cooldown","model":"gemini-3.8-flash",
"reset_seconds":51,"retry_after":"2026-09-30T21:13:58.892Z","credentials_cooling":1}}
```

The operator's exclusion reason is substantiated: the `gemini/*` leg is in a
permanent 429/cooldown loop and is the single largest consumer of the gateway's
resilience machinery.

### 5.2 Every `gemini/*` leg (registry spelling) and its head status

`gemini/*` appears only as `gemini/gemini-3.8-flash` (provider `google_ai_studio`,
`omniroute_id: gemini`, `tier: free`, `trains_on_prompts: true`):

| route | position | role | action |
|---|---|---|---|
| `gemini-3.8-flash` | leg 1/3 (sole live leg) | **head of a pinned per-model combo** | **repoint** → `vertex/gemini-3.8-flash` |
| `t1-orchestrator` | leg 1/9 | head | replace with `vertex/gemini-3.8-flash` (1M credit) |
| `t1-orchestrator-free-only` | leg 2/5 | head (-free-only) | **remove** (vertex is credit → not free-only); the 128k free legs remain |
| `t2-worker` | leg 1/19 | head | **remove**; new head `antigravity/gemini-3.7-flash-high` |
| `t2-worker-free-only` | leg 1/9 | head | **remove**; new head `huggingface/zai-org/GLM-5.2` |

No other route carries a `gemini/*` leg (verified by script over
`routes.*.legs`). The models row `gemini-3.8-flash` and provider
`google_ai_studio` stay in the registry (other surfaces may reference them).

### 5.3 Repoint target proven

`vertex/gemini-3.8-flash` (provider `vertex_ai`, `tier: credit`,
`trains_on_prompts: false`, live `/v1/models` ctx 1048576) was probed
2026-09-30T21:13:23Z — record in `logs/probe-free-extra-20260930.jsonl`: ack HTTP
200 (5.97 s, content empty = reasoning budget), **tool-call ok (1 call, 2.27 s)**.
So the repoint preserves the exact model with a working, non-429 door.

### 5.4 Gate `google_ai_studio` while it is dead

Add to `providers.google_ai_studio`:
```jsonc
"available": false,
"unavailable_until": "2026-10-07T00:00:00Z",
"$comment": "FREEPROBE 2026-09-30: 8088 gemini cooling-down lines / 7187 lastErrorCode=429 today; live probe 429 model_cooldown. Re-probe after the until."
```
(rule 8 requires `available:false` whenever `unavailable_until` is set).

### 5.5 `combos.json` `$comment` / drift

The `T1FREE` note in `combos.json`'s top `$comment` ("keep a free
gemini/gemini-3.8-flash fallback leg") is superseded; `render omniroute`
regenerates the file, so confirm with `tools/registry.py render omniroute --check`
after the registry edit. The `openhands`/`ide` renders (`catalog/ide-models.json`,
`configuration/openhands/tier-profiles.json`, `docs/models.md`) must be
regenerated too if a `gemini-3.8-flash` combo changes name or context.

---

## 6. (d) 429-policy proposal — v3.8.50 **does** support cooldown / rotation / breaker config

**Answer:** yes. `OMNIROUTE_ROTATION_*` exists, plus a per-status
`OMNIROUTE_ROTATE_*` family, plus per-provider circuit-breaker envs. A
skip-on-repeated-429 policy is configurable without a code change.

### 6.1 Rotation / account-fallback gate — `open-sse/services/rotationConfig.ts`

| env var | default | file:line | meaning |
|---|---|---|---|
| `OMNIROUTE_ROTATION_ENABLED` | `true` | `open-sse/services/rotationConfig.ts:84` | master switch; `false` blocks fallback for the gated 429/500/502 classes |
| `OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS` | `0` (= engine default) | `:85` | cooldown applied to a rate-limited account when upstream gives no hint |
| `OMNIROUTE_ROTATION_DISABLE_TAG_WITHOUT_RESET` | `true` | `:86` | don't tag rate-limited without a reset time |
| `OMNIROUTE_ROTATE_ON_429` | `true` | `:88` | rotate/fall back on a 429 at all |
| `OMNIROUTE_ROTATE_429_THRESHOLD` | `1` (= immediate) | `:89` | **number of 429s within the window before rotation** |
| `OMNIROUTE_ROTATE_429_WINDOW_SECONDS` | `120` | `:90` | sliding window for the count |
| `OMNIROUTE_ROTATE_ON_{500,502,400}`, `OMNIROUTE_ROTATE_{500,502,400}_{THRESHOLD,WINDOW_SECONDS}` | 500/502 on, 400 off | `:93`–`:110` | same gate per status |

Counting/decision logic: `recordErrorAndCheckThreshold()` `:251`–`:273`,
`evaluateRotationGate()` `:295`–`:322`, `classForStatus()` `:191`–`:197`,
`isFallbackBlockedForStatus()` `:209`–`:215`. A `threshold > 1` **holds
fallback for the first N−1 errors and rotates only on the Nth inside the
window** — i.e. exactly "skip the single 429, act on repeated 429s". Per-leg
override is also possible via a connection's
`providerSpecificData.rotationOverrides.{rotateOn429,error429Threshold,error429WindowSeconds,rateLimitResetSeconds}`
(`resolveRotationConfig()`, `:160`–`:188`).

### 6.2 Provider circuit breaker — `open-sse/config/constants.ts`

| env var | default | file:line |
|---|---|---|
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_FAILURE_THRESHOLD` | `15` | `open-sse/config/constants.ts:266` |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_FAILURE_WINDOW_MS` | `1800000` (30 min) | `:267-270` |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS` | `600000` (10 min) | `:271` |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_DEGRADATION_THRESHOLD` | `7` | `:272` |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_MAX_BACKOFF_MULTIPLIER` | `4` | `:273` |
| `OMNIROUTE_PROVIDER_BREAKER_API_KEY_BACKOFF_ESCALATION_COUNT` | `3` | `:274-277` |
| OAuth twin (`…_OAUTH_*`) | 10 / 900000 / 300000 | `:251-257` |

Breaker engine: `src/shared/utils/circuitBreaker.ts` — options `cooldownByKind`
`:93`, `kindThresholds` `:99`, `immediateOpen` `:84`, `_effectiveCooldown()` `:446`,
`_onFailure()` kind thresholds `:391-407`. The 429-vs-quota classifier is
`src/shared/utils/classify429.ts:257` (`classify429`), body patterns `:31-94`.

### 6.3 Where the current gemini cooling comes from

The per-model account cooldown is `src/sse/services/auth.ts:1679-1699`
(`"{provider} | all N active accounts cooling down for model {model} …"`,
`lastErrorCode=429`). Upstream-429 hint trust defaults per provider in
`src/shared/utils/providerHints.ts` (`defaultUseUpstream429BreakerHints`,
~`:44-62`) and is consumed by the breaker in
`src/sse/handlers/chat.ts:1494-1516` (`cooldownByKind {rate_limit: 60_000,
quota_exhausted: 3_600_000}`) and `src/sse/handlers/chatHelpers.ts:359-367`.

### 6.4 Proposed policy (gateway env / settings — proposal only)

```
OMNIROUTE_ROTATION_ENABLED=true
OMNIROUTE_ROTATE_ON_429=true
OMNIROUTE_ROTATE_429_THRESHOLD=3          # hold the first two 429s; rotate on the third
OMNIROUTE_ROTATE_429_WINDOW_SECONDS=120
OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS=300   # 5-min cooldown once rotated
OMNIROUTE_PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000   # provider-level, 30 min
```

These are gateway-process env vars (set where the gateway is launched, e.g.
the compose/`.env`), **not** AutoOS registry fields — so this proposal targets
the stack launcher, not `catalog/ai-registry.json`. The safest first step is the
per-leg `rotationOverrides` on the gemini connection (no global behaviour
change), then the env policy once measured.

---

## 7. (e) Exclusion list with verbatim reasons

| Candidate | Verdict | Verbatim evidence (measured 2026-09-30) |
|---|---|---|
| `opencode` / `opencode-zen` | **exclude** — vendor blocks free outside OpenCode | `[402]: This model requires an opencode API key - add one in Settings → Providers.` (`opencode-zen/deepseek-v4.1-flash` and `opencode/deepseek-v4.1-flash`); the gateway findings doc §7 records the 403 `"OpenCode's free tier can only be used from within OpenCode"` |
| `navyai` (`navy`) | **exclude confirmed** — no usable free model | Cloudflare **Error 1010** `browser_signature_banned` (zone `api.navy`), `owner_action_required:true`, `retryable:false`, `"**Do not retry.** Your user-agent has been banned by the site owner."` on all 3 legs. FREEKEYS-1 (2026-09-28): `6 tried, 400 x5, 403 x1` |
| `bluesminds` (`bm`) | **exclude confirmed** — no live free model | `Model 'glm-4.7' is not available in the active live catalog for provider 'bluesminds'.` / same for `deepseek-chat`; `bm/gpt-oss-20b` 504. FREEKEYS-1: `400 x4, 410 x1, ERR x1` |
| `arcee` | **exclude confirmed** — no key / no models | `/api/providers`: connection `arcee-ai` `testStatus: unknown`, `errorCode: missing_api_key`, `lastErrorType: auth_missing`; `/v1/models` exposes **0** `arcee/*` ids |
| `cheaperinference` | **recheck: 402 lifted, but PAID** | wallet 402 is **gone**: `cheaperinference/glm-5.2` ack 200 / tool ok, `kimi-k3` tool ok, `deepseek-v4-flash` ack+tool ok. Registry `tier: paid`; `deny-deepseek` blocks `deepseek-v4-flash`. → credit band, **not a free model** |
| `antigravity` / `claude` (OAuth) | **exclude** — OAuth expired, operator reconnect | `antigravity/claude-opus-4-6-thinking` 401 `[antigravity] All 1 connection(s) authentication expired`; `/api/providers` `antigravity` → `unrecoverable_refresh_error`, `Refresh token rejected … Please re-authenticate this account.`; `claude` → `no_refresh_token`, `No refresh token available — re-authenticate this account.` |
| `devin` | **exclude** — OAuth/ACP pending | `devin/devin` 502 `Devin ACP error -32603: Failed to load team settings: … Authentication required: invalid api key` |
| `qoder` | **exclude** — OAuth pending | no `qoder/*` id in the live `/v1/models`; docs/models-proposed.md D2: "pending" (binary + PAT; gateway connection dashboard-only) |
| `cerebras` | fail — connection inactive | `No active credentials for provider: cerebras.` (401); `/api/providers` `cerebras` `isActive: false` |
| `cloudflare-ai` | fail — config gap (free but unwired) | `Cloudflare Workers AI requires an Account ID. Add it in provider settings under 'Account ID'.` (502) |
| `sambanova` | fail — key invalid | `[401]: Incorrect API key provided: &lt;masked-key-prefix&gt;.`; connection `testStatus: expired` |
| `together` | fail — fingerprint ban | Cloudflare Error 1010 `browser_signature_banned` (zone `api.together.xyz`), `retryable:false` |
| `bazaarlink` (`bzl`) | fail — credits exhausted | `[402]: Insufficient credits. Please top up to continue.`; `[bazaarlink] All 1 connection(s) credits exhausted` |
| `novita` | fail — fingerprint ban / per-model forbidden | Error 1010 (`api.novita.ai`) for `zai-org/glm-5.2`; `Model deepseek/deepseek-v4.1-flash forbidden (per-model access/subscription)` |
| `agentrouter` | fail — not an OpenAI function-call API | ack 200 but tool 400: `Failed to deserialize the JSON body … tools[0]: unknown variant 'custom', expected 'web_search_20250305' or 'web_search_20260209'` |
| `openrouter/z-ai/glm-5.2:free` | fail — not in live catalog | `Model 'z-ai/glm-5.2:free' is not available in the active live catalog for provider 'openrouter'.` (400) |
| `openrouter/google/gemma-4-31b-it:free` | fail — empty upstream | tool 502 `upstream returned an empty response without usable output` |
| `openrouter/thinkingmachines/inkling:free` | fail — harness-gated | `[403]: thinkingmachines/inkling:free is only available on agentic harnesses.` |

---

## 8. Admission / rate-limit log (UTC, verbatim)

| # | UTC | leg | status | backoff |
|---|---|---|---|---|
| 1 | 2026-09-30T20:42:23Z | `bm/gpt-oss-20b` (ack) | 504 | 60 s |
| 2 | 2026-09-30T20:45:25Z | `bm/gpt-oss-20b` (ack) | 504 | 120 s |
| 3 | 2026-09-30T20:51:31Z | `bm/gpt-oss-20b` (tool) | 504 | 60 s |
| 4 | 2026-09-30T20:54:34Z | `bm/gpt-oss-20b` (tool) | 504 | 120 s |
| 5 | 2026-09-30T21:01:35Z | `openrouter/google/gemma-4-31b-it:free` (ack) | 504 | 60 s |
| 6 | 2026-09-30T21:03:42Z | `openrouter/google/gemma-4-31b-it:free` (ack) | 504 | 120 s |
| 7 | 2026-09-30T21:07:42Z | `opencode-zen/deepseek-v4.1-flash` (ack) | 429 | 60 s |
| 8 | 2026-09-30T21:08:42Z | `opencode-zen/deepseek-v4.1-flash` (ack) | 429 | 120 s |

Source: `logs/probe-free-20260930.jsonl` (the 8 backoff lines). The separate
`gemini/gemini-3.8-flash` run retried twice more — `2026-09-30T21:14:03Z` (429,
60 s) and `2026-09-30T21:15:08Z` (429, 120 s) — captured on that run's stdout in
`logs/probe-free-extra-20260930.jsonl` (it was not appended to the main log).

No `chat_admission_busy` was seen. Every 429 landed on the excluded/cooldown
legs (opencode-zen, gemini), never on a candidate this lane recommends.

---

## 9. Cross-family review

Three `t3-reviewer` subagents ran read-only on this document, on three different
families, **including one free model** (operator mandate). No reviewer edited
anything. Session ids are the subagent sessions.

| # | Reviewer model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/deepseek-v4.1-flash` | DeepSeek | APPROVED-WITH-NOTES | ses_f0bd14207ffe6ygVuItcGz9hy1 |
| 2 | `omniroute/vertex-3.8-flash` | Gemini (Vertex) | APPROVED-WITH-NOTES | ses_f0bd14205ffedWdnU2F4QiOXJy |
| 3 | `openrouter/poolside/laguna-s-2.1:free` | Poolside (**free**) | APPROVED-WITH-NOTES | ses_f0bd14203ffeE9kz3g7HzRjw12 |

What each read / verified:

- **#1 (DeepSeek):** the whole doc, `logs/probe-free-20260930.jsonl` (counted 43
  records + 8 backoff lines), `logs/gemini-log-analysis-20260930.txt`, and the
  v3.8.50 sources for every §6.1/§6.2 citation. Confirmed all §2 numbers and all
  §6.1/§6.2 file:line citations verbatim; found the §5.1 all-provider count and
  the §2 summary count wrong (below).
- **#2 (Vertex):** the doc against `catalog/ai-registry.json`, `tools/registry.py`,
  `catalog/ai-registry.schema.json`. Confirmed `resolve_leg` first-slash
  splitting, the `deny-groq`/`deny-openrouter` first-match behaviour, and that
  §5.2 lists all 5 `gemini/*` routes. Found schema-required model keys missing,
  the `IDE_MODEL_ORDER`/`OPENHANDS_TIER_ORDER` render gates, and the
  provider-interleaving gap.
- **#3 (Poolside, free):** the doc against the JSONL (8+ rows across every
  failure class) and greps for secrets. Confirmed the §3 proven list matches the
  `tool=ok` rows, no key fragment / email / username, and no unsupported claim.
  Found the §8 rows 9–10 not present in the JSONL, the §5.1 percentage error,
  the §2 count error, and asked for a §5.3 source citation.

All notes applied before this commit:

1. §5.1 all-provider count → `8218` and share → `98.4%` (was `8169` / `94.7%`).
2. §2 summary count → "13 proven, 1 tool-schema fail, **29** credential/config/
   transport failures" (was "13/43 failed").
3. §8 rows 9–10 (gemini backoff) removed from the "verbatim JSONL" table and
   moved to a note citing `logs/probe-free-extra-20260930.jsonl` (that run's
   stdout, not the main log).
4. §4.1 model rows now carry every schema-required key (`context_usable`,
   `reasoning`, `effort_ladder`, `tool_calls`).
5. §4.3 new-render-gate caveat added (`IDE_MODEL_ORDER`,
   `OPENHANDS_TIER_ORDER` — or omit `openhands`/`openhands_profile`).
6. §4.4 free-band blocks re-ordered interleaved (no two adjacent legs share a
   provider), and the moved/deduped `groq` legs are noted.
7. §5.3 now cites `logs/probe-free-extra-20260930.jsonl`.

---

## 10. Files changed

| File | Change |
|---|---|
| `tools/probe-free.py` | NEW — ack + single-tool-call probe, 60/120 s backoff, key never printed |
| `tools/analyse-gemini-log.py`, `tools/analyse-gemini-codes.py` | NEW — filtered gemini cooling/429 counters (no log values quoted) |
| `tools/gw-leg-meta.py`, `tools/gw-free-rows.py`, `tools/gw-model-rows.py`, `tools/gw-providers.py`, `tools/gw-raw-call.py`, `tools/gw-get.py` | NEW — read-only gateway metadata/connection readers |
| `tools/check-legs.py`, `tools/dump-routes.py`, `tools/dump-prov-comments.py`, `tools/matrix.py` | NEW — registry inspection helpers |
| `docs/handoff/2026-09-30-laneFreeProbe.md` | NEW — this evidence doc |
| `logs/handoff-sessions/DONE-ws-free-probe.md` | NEW — DONE note |
| `logs/probe-free-20260930.jsonl`, `logs/probe-free-extra-20260930.jsonl`, `logs/gemini-log-analysis-20260930.txt` | git-ignored — raw probe + separate vertex/gemini run + filtered log counts (`git add -f`) |
| `catalog/ai-registry.json`, `configuration/omniroute/combos.json` | **NOT edited** — proposals handed to the single-writer wiring lane |
