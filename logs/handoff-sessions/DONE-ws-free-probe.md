# DONE — L1-backlog/ws-free-probe-20260930 (free-provider wiring probe)

**Lane:** `L1-backlog/ws-free-probe-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-free-probe`
**Base:** `origin/main` @ `e58274a`
**Date:** 2026-09-30 (measured 20:34–21:13Z)
**Writer:** free-probe (pinned)

Read-only probe of every candidate free-tier provider/model through the live
OmniRoute gateway `127.0.0.1:20128` (v3.8.50), plus the gemini health/exclusion
evidence and the 429-policy capability check. **No routing config was edited** —
`catalog/ai-registry.json` and `configuration/omniroute/combos.json` are
untouched; the wiring proposal is in the evidence doc.

---

## 1. Evidence doc

`docs/handoff/2026-09-30-laneFreeProbe.md`

## 2. Leg × result (43 legs, measured)

| Verdict | Legs |
|---|---|
| **proven** (tool-call ok) | `groq/openai/gpt-oss-120b`, `groq/openai/gpt-oss-20b`, `groq/qwen/qwen3.8-27b`, `huggingface/zai-org/GLM-5.2`, `huggingface/Qwen/Qwen3.8-27B`, `openrouter/qwen/qwen3.8-27b:free`, `openrouter/nvidia/nemotron-3-super-120b-a12b:free`, `openrouter/cohere/north-mini-code:free`, `openrouter/poolside/laguna-s-2.1:free` |
| **proven but paid/credit** (not free) | `cheaperinference/glm-5.2`, `cheaperinference/kimi-k3`, `cheaperinference/deepseek-v4-flash`, `vertex/gemini-3.8-flash` |
| **ack-only** (no tool support) | `agentrouter/deepseek-v4-flash` |
| **failed** (29) | cerebras ×2 (401 no creds), cloudflare-ai ×4 (502 no Account ID), sambanova ×3 (401 expired key), together ×2 (403 CF 1010), novita ×2 (403 CF 1010 / per-model), navy ×3 (403 CF 1010), bzl ×3 (400/402/401 credits), bm ×3 (400/504), openrouter z-ai/gemma/inkling (400/502/403), opencode + opencode-zen (402), antigravity (401), devin (502) |

**13/43 proven**; 3 free bands are policy-gated today: `groq/*` by `deny-groq`,
`openrouter/*` by `deny-openrouter`. `huggingface/*` has no gate.

## 3. Wiring proposal (single-writer lane applies)

- **New single-provider combos** for each proven free leg (registry route +
  rendered `combos.json` entry) — evidence doc §4.3.
- **New model rows** for `Qwen/Qwen3.8-27B`, `qwen/qwen3.8-27b:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`, `cohere/north-mini-code:free`,
  `poolside/laguna-s-2.1:free` — §4.1 (schema-required keys included).
- **Policy allow rules** above `deny-groq` / `deny-openrouter` — §4.2
  (operator decision: the single-tool-call probe does **not** overturn deny-groq's
  multi-turn 400/413 history).
- **Free-band insertion** into `t2-worker` / `t3-driver` (interleaved, before the
  credit/paid tail) and `*-free-only` (free legs only) — §4.4.
  **Never `*-clean`.**

## 4. Gemini removal plan (§5)

5 routes carry `gemini/gemini-3.8-flash` (`gemini-3.8-flash` head,
`t1-orchestrator`, `t1-orchestrator-free-only`, `t2-worker`,
`t2-worker-free-only`). Repoint the pinned group to the proven
`vertex/gemini-3.8-flash`; remove the leg from the tier routes; gate
`google_ai_studio` with `available:false` + `unavailable_until`. Health:
**8088** gemini cooling-down lines / **7187** `lastErrorCode=429` on 2026-09-30
(operator reported ~7242 / ~6766 — same order, log still growing); live probe 429
`model_cooldown`.

## 5. 429 policy (§6)

v3.8.50 **does** support it: `OMNIROUTE_ROTATION_ENABLED`,
`OMNIROUTE_ROTATE_ON_429`, `OMNIROUTE_ROTATE_429_THRESHOLD`,
`OMNIROUTE_ROTATE_429_WINDOW_SECONDS`,
`OMNIROUTE_ROTATION_RATE_LIMIT_RESET_SECONDS`
(`open-sse/services/rotationConfig.ts:84-110`) and the provider breaker family
(`open-sse/config/constants.ts:251-277`), engine in
`src/shared/utils/circuitBreaker.ts`. Proposed skip-on-repeated-429:
`ROTATE_429_THRESHOLD=3`, `WINDOW_SECONDS=120`,
`RATE_LIMIT_RESET_SECONDS=300`, `PROVIDER_BREAKER_API_KEY_COOLDOWN_MS=1800000`
(gateway env — proposal only).

## 6. Exclusions (§7)

`opencode`/`opencode-zen` (402 "requires an opencode API key"; vendor blocks free
outside OpenCode), `navyai`/`bluesminds`/`arcee` (confirmed: navy 403 CF 1010
browser-signature ban; bm model-not-in-catalog; arcee `missing_api_key`, 0
models), `cheaperinference` (**recheck: 402 lifted — now serves, but paid, not
free**), `antigravity`/`claude` (OAuth expired → operator reconnect), `devin`
(ACP 502 invalid key), `qoder` (pending), `cerebras`/`cloudflare-ai`/`sambanova`/
`together`/`bazaarlink`/`novita`/`agentrouter` (verbatim reasons in §7).

## 7. Reviews (operator mandate: ≥1 free model)

| # | Model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/deepseek-v4.1-flash` | DeepSeek | APPROVED-WITH-NOTES | ses_f0bd14207ffe6ygVuItcGz9hy1 |
| 2 | `omniroute/vertex-3.8-flash` | Gemini (Vertex) | APPROVED-WITH-NOTES | ses_f0bd14205ffedWdnU2F4QiOXJy |
| 3 | `openrouter/poolside/laguna-s-2.1:free` | Poolside (**free**) | APPROVED-WITH-NOTES | ses_f0bd14203ffeE9kz3g7HzRjw12 |

All notes applied (2 count fixes, 1 percentage fix, 1 log-source fix, schema-
required model keys, render-gate caveat, provider interleaving).

## 8. Backoffs

8 backoff lines in the main log (bm/gpt-oss-20b 504 ×4, gemma :free 504 ×2,
opencode-zen 429 ×2) + 2 on the separate gemini run — all in the evidence doc §8.
No `chat_admission_busy`.

## 9. Files

| File | Change |
|---|---|
| `docs/handoff/2026-09-30-laneFreeProbe.md` | NEW — evidence + proposal |
| `logs/handoff-sessions/DONE-ws-free-probe.md` | NEW — this note |
| `tools/probe-free.py` | NEW — gateway probe |
| `tools/analyse-gemini-log.py`, `tools/analyse-gemini-codes.py` | NEW |
| `tools/gw-leg-meta.py`, `tools/gw-free-rows.py`, `tools/gw-model-rows.py`, `tools/gw-providers.py`, `tools/gw-raw-call.py`, `tools/gw-get.py` | NEW — read-only gateway readers |
| `tools/check-legs.py`, `tools/dump-routes.py`, `tools/dump-prov-comments.py`, `tools/matrix.py` | NEW — registry helpers |
| `logs/probe-free-20260930.jsonl`, `logs/probe-free-extra-20260930.jsonl`, `logs/gemini-log-analysis-20260930.txt` | git-ignored (`git add -f`) |
| `catalog/ai-registry.json`, `configuration/omniroute/combos.json` | **NOT edited** |

## 10. What remains (single-writer wiring lane)

1. Apply §4 (combos + model rows + policy rules + free-band insertion) to
   `catalog/ai-registry.json`, then `python tools/registry.py render omniroute`
   (and `render ide` / `render openhands` / `render models-doc` if routes change).
2. Operator decision on lifting `deny-groq` (re-probe a multi-turn agent shape
   first) and on `openrouter/*:free` allow rules.
3. Apply the gemini removal plan (§5).
4. Apply the 429 env policy (§6) at the gateway launcher.
5. Reconnect `sambanova` (expired key) and add a Cloudflare Account ID if those
   free legs are wanted.
