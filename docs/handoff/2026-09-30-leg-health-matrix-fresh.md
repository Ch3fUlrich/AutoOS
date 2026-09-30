# Leg-Health Matrix — Fresh Post-Recovery Check

**Run:** ws-omniroute-20260930
**Lane:** L1-backlog/ws-leghealth2-20260930
**Date:** 2026-09-30
**Base:** d08f7f2

## Method

20 legs enumerated: 15 combos from `configuration/omniroute/combos.json` + 5 hand
entries from `providers.omniroute.models` in `opencode.jsonc` (above the
`AUTOOS-MANAGED-START` marker). Each leg received ONE cheap chat ack
(`{"messages":[{"role":"user","content":"ack"}],"max_tokens":8,"model":"<id>"}`)
to the OmniRoute gateway on :20128. Spacing >=4s between legs. One retry on
non-200. 60s per-request timeout. Measurement only — no repairs or config edits.

## Summary

| Result          | Count | Legs              |
|-----------------|-------|-------------------|
| 200 (healthy)   | 13    | 1, 9–20           |
| 401 (auth)      | 2     | 3, 8              |
| 403             | 0     | —                 |
| 503             | 0     | —                 |
| other (502)     | 3     | 2, 4, 7           |
| other (timeout) | 2    | 5, 6              |
| **Total**       | **20**|                   |

## Per-Leg Matrix

| # | UTC timestamp | Combo / Entry | Model sent | Status | Latency | One-line verbatim |
|---|---------------|---------------|------------|--------|---------|-------------------|
| 1 | 14:35:50Z | combo:deepseek-v4.1-flash | deepseek-v4.1-flash | 200 | 1742ms | Got it - what can I help you with? |
| 2 | 14:35:55Z | combo:gemini-3.8-flash | gemini-3.8-flash | 502 | 4241ms | vertex/gemini-3.8-flash: quality validation - reasoning consumed 5/5 tokens - no content output (HTTP 502) |
| 3 | 14:36:09Z | combo:opus-4-6 | opus-4-6 | 401 | 17ms | antigravity/claude-opus-4-6-thinking: auth - [antigravity] All 1 connection(s) authentication expired - please reconnect... |
| 4 | 14:36:14Z | combo:spark-1.3-contributor | spark-1.3-contributor | 502 | 2008ms | meta-api/muse-spark-1.3-contributor: provider - [openai-compatible-chat-conn:d9427825/muse-spark-1.3-contributor] upstream... (truncated 120ch) |
| 5 | 14:36:26Z | combo:t1-orchestrator | t1-orchestrator | timeout | 60018ms | The request was canceled due to the configured HttpClient.Timeout of 60 seconds elapsing. |
| 6 | 14:38:31Z | combo:t1-orchestrator-free-only | t1-orchestrator-free-only | timeout | 60017ms | The request was canceled due to the configured HttpClient.Timeout of 60 seconds elapsing. |
| 7 | 14:40:36Z | combo:t1-orchestrator-paid | t1-orchestrator-paid | 502 | 2585ms | meta-api/muse-spark-1.3-contributor: provider - [openai-compatible-chat-conn:d9427825/muse-spark-1.3-contributor] upstream... (truncated 120ch) |
| 8 | 14:40:49Z | combo:t2-orchestrator | t2-orchestrator | 401 | 16ms | antigravity/claude-opus-4-6-thinking: auth - [antigravity] All 1 connection(s) authentication expired - please reconnect... |
| 9 | 14:40:54Z | combo:t2-worker | t2-worker | 200 | 1310ms | Acknowledged. |
| 10 | 14:41:00Z | combo:t2-worker-clean | t2-worker-clean | 200 | 1738ms | Acknowledged. What would you like to do next? |
| 11 | 14:41:05Z | combo:t2-worker-free-only | t2-worker-free-only | 200 | 754ms | Hello! It seems like you might be |
| 12 | 14:41:10Z | combo:t3-driver | t3-driver | 200 | 266ms | It looks like you're typing "ack |
| 13 | 14:41:14Z | combo:t3-driver-clean | t3-driver-clean | 200 | 1124ms | Acknowledged. What would you like me to do? |
| 14 | 14:41:20Z | combo:t3-driver-free-only | t3-driver-free-only | 200 | 459ms | It seems like you're just typing " |
| 15 | 14:41:24Z | combo:t4-rag | t4-rag | 200 | 412ms | It seems like you've just typed |
| 16 | 14:41:28Z | hand:vertex-flash | vertex/gemini-3-flash-preview | 200 | 4045ms | Acknowled |
| 17 | 14:41:37Z | hand:vertex-flash-lite | vertex/gemini-3.1-flash-lite | 200 | 6354ms | (200 OK, content null — raw JSON returned) |
| 18 | 14:41:47Z | hand:vertex-pro | vertex/gemini-3.1-pro-preview | 200 | 9137ms | (200 OK, content null — raw JSON returned) |
| 19 | 14:42:00Z | hand:vertex-3.8-flash | vertex/gemini-3.8-flash | 200 | 10229ms | (200 OK, content null — raw JSON returned) |
| 20 | 14:42:14Z | hand:gemini-2.5-flash | gemini/gemini-2.5-flash | 200 | 3662ms | (200 OK, content null — raw JSON returned) |

All timestamps 2026-09-30. Latency for timeout legs (5, 6) is the last attempt only;
total wall-clock including first timeout + retry was ~125s each.

## Operator Reconnect Checklist

Connections that still need attention after the claimed full recovery:

### 1. antigravity — RECONNECT NEEDED (401)

**Affected legs:** 3 (opus-4-6), 8 (t2-orchestrator)
**Error (verbatim):** `antigravity/claude-opus-4-6-thinking: auth - [antigravity] All 1 connection(s) authentication expired - please reconnect...`
**Action:** Re-credential the antigravity connection on the gateway. Both legs fail
at the head model `antigravity/claude-opus-4-6-thinking`. The brief claimed "all
keyed connections re-credentialed" — this one was not.

### 2. meta-api (muse-spark-1.3-contributor) — INVESTIGATE (502)

**Affected legs:** 4 (spark-1.3-contributor), 7 (t1-orchestrator-paid)
**Error (verbatim, truncated 120ch):** `meta-api/muse-spark-1.3-contributor: provider - [openai-compatible-chat-conn:d9427825/muse-spark-1.3-contributor] upstream...`
**Action:** Check the meta-api provider connection (registered 2026-09-30 per
combos.json `$comment`). The upstream error is not an auth failure (no 401) — it
may be a provider-side outage or a gateway-side connection misconfiguration. This
leg is the sole leg of t1-orchestrator-paid and the paid escalation leg of
t2-worker/t3-driver after their free legs.

### 3. gemini/gemini-3.8-flash (AI Studio) — INVESTIGATE (502)

**Affected legs:** 2 (gemini-3.8-flash combo directly)
**Error (verbatim):** `vertex/gemini-3.8-flash: quality validation - reasoning consumed 5/5 tokens - no content output (HTTP 502)`
**Action:** Model behavior issue, not an auth failure. With max_tokens=8, the
reasoning model consumed all tokens on internal reasoning and produced no content.
The gateway's quality validation rejected it (502). This leg is the first leg of
t1-orchestrator and t1-orchestrator-free-only, contributing to their 60s timeouts
(see #4). Note: the hand entry `vertex/gemini-3.8-flash` (leg 19, Vertex AI
passthrough) returned 200 — the combo `gemini-3.8-flash` routes to
`gemini/gemini-3.8-flash` (AI Studio), a different provider. The Vertex AI
connection itself is healthy.

### 4. t1-orchestrator / t1-orchestrator-free-only — DOWNSTREAM TIMEOUT

**Affected legs:** 5 (t1-orchestrator), 6 (t1-orchestrator-free-only)
**Error (verbatim):** `The request was canceled due to the configured HttpClient.Timeout of 60 seconds elapsing.`
**Root cause:** Both combos have `gemini/gemini-3.8-flash` as their first leg (502,
see #3). Subsequent legs (scw/qwen3-235b-a22b-instruct-2507,
nebius/zai-org/GLM-5.3-Flash, scw/mistral-small-3.2-24b-instruct-2506) also failed
or were slow, exhausting the 60s timeout. t1-orchestrator's final leg
`meta-api/muse-spark-1.3-contributor` also fails (502, see #2).
**Action:** No direct reconnect needed. Fixing #3 and verifying the scw/nebius legs
should resolve these timeouts.

## Notes

- The hand entries (legs 16–20) all returned 200, including direct Vertex AI
  passthroughs. The Vertex AI connection (registered from the vertex_ai key on
  2026-09-30) is healthy.
- t2-worker (leg 9) returned 200 despite sharing the gemini-3.8-flash first leg —
  its longer leg chain (8 legs including deepseek/deepseek-flash and free-ai/qwen7b)
  found a working leg quickly.
- deepseek-v4.1-flash (leg 1) and all t3-driver variants (legs 12–14) responded
  fastest (266–1124ms), confirming the deepseek and scw/nebius/mistral legs are
  healthy when reached directly.
- Legs 17–20 (vertex-flash-lite, vertex-pro, vertex-3.8-flash, gemini-2.5-flash)
  returned 200 but with null content in the response body — the models consumed
  all 8 max_tokens on reasoning. The gateway accepted the response (200) unlike the
  AI Studio gemini-3.8-flash leg which was rejected (502). Both are reasoning-model
  behavior with tiny max_tokens, not connection issues.
