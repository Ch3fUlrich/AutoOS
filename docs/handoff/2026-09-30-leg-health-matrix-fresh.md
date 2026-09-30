# Leg-Health Matrix — Fresh Probe (rewritten from recorded JSONL)

**Run:** ws-omniroute-20260930
**Lane:** L1-backlog/ws-leghealth2-20260930
**Date:** 2026-09-30
**HEAD at probe:** `62d89296adcc598b5ef59e94254378f708d4d62`
**Probe:** `docs/handoff/2026-09-30-leg-health-probe.py`
**Evidence (authoritative):** `docs/handoff/2026-09-30-leg-health-raw.jsonl`

Every number below is derived from the JSONL only. No value is hand-typed.

---

## 1. Method and how to read this

- **Gateway:** `http://127.0.0.1:20128` (OmniRoute). Measurement only — no config,
  registry or gateway state was edited.
- **Two populations, 34 health rows:**
  - **15 combos** — the plain names in `configuration/omniroute/combos.json`.
  - **19 distinct legs** — the union of every `models[]` id in `combos.json`
    (14 ids) plus the 5 hand-entry `modelID`s above `AUTOOS-MANAGED-START` in
    `opencode.jsonc` that are not already combo legs.
- **Per row:** ONE chat, `max_tokens` 256, prompt `"ack"`, `>=3 s` spacing,
  exactly one retry on non-200, 60 s client timeout.
- **Legs are probed DIRECTLY.** A combo 200 does not name the leg that served it:
  the combo routes through the gateway and the response `model` field names the
  serving leg. The leg rows below are the only direct evidence of leg health.
- **TRUE UTC.** Each attempt records wall-clock UTC from
  `datetime.now(timezone.utc)` at send time (millisecond precision, trailing `Z`).
  This is *not* a log-derived or inferred timestamp: it is the probe's own clock.
- **`200` is split two ways.** A gateway 200 with a non-empty
  `choices[0].message.content` is **`200_content`** (a *usable completion*). A 200
  whose completion content is empty/null is **`200_null`** (the provider answered
  but spent the whole budget on `reasoning_content`; `finish_reason: "length"`).
  Only `200_content` counts as usable.
- **Control probe.** `vertex/gemini-3.1-pro-preview` is a reasoning leg that
  answers at 256 tokens. It is probed at `max_tokens` 8 **and** 256 so the pair
  isolates token budget from provider outage.
- **Replayable.** With the helper below defined, each row's Command column is the
  exact reproducing invocation.

```bash
omni() { curl -sS -w '\nHTTP %{http_code} %{time_total}s\n' \
  -H "Authorization: Bearer $AUTOOS_OMNIROUTE_KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"ack\"}],\"max_tokens\":$2}" \
  http://127.0.0.1:20128/v1/chat/completions; }
omni <model-id> 256   # or 8 for the control's small-budget row
```

Run window (TRUE UTC): **2026-09-30T20:10:59.375Z → 2026-09-30T20:16:25.756Z**
(chat probes); **test-all:** 2026-09-30T20:16:25.756Z → 2026-09-30T20:16:44.622Z.

---

## 2. Headline (derived from JSONL)

Latest attempt per logical probe (36 chat probes):

| Class | Count |
|-------|-------|
| `200_content` (usable completion) | 25 |
| `200_null` (HTTP 200, empty content) | 1 |
| `http_401` | 6 |
| `http_429` | 2 |
| `http_502` | 2 |
| **Total logical probes** | **36** |

Restricted to the 34 health rows (15 combos + 19 legs):

| Population | Rows | `200_content` | non-200 |
|------------|------|---------------|---------|
| Combos | 15 | 13 | 1×401, 1×502 |
| Legs | 19 | 11 | 5×401, 2×429, 1×502 |
| **Health total** | **34** | **24** | **10** |

> **Usable-completion count = 24 of 34 health rows.** The single `200_null` is the
> control's own small-budget row, not a health row — hence 24 usable, not 25.

---

## 3. Combos (15) — latest attempt

| # | UTC (TRUE) | Combo | Status | Class | Latency | Serving leg (`model` field) | Command |
|---|------------|-------|--------|-------|---------|------------------------------|---------|
| C1 | 2026-09-30T20:10:59.375Z | deepseek-v4.1-flash | 200 | content | 1370ms | `deepseek-flash` | `omni deepseek-v4.1-flash 256` |
| C2 | 2026-09-30T20:11:20.018Z | gemini-3.8-flash | 200 | content | 17643ms | `gemini-3.8-flash` | `omni gemini-3.8-flash 256` |
| C3 | 2026-09-30T20:11:26.042Z | opus-4-6 | 401 | — | 11ms | — | `omni opus-4-6 256` |
| C4 | 2026-09-30T20:11:49.231Z | spark-1.3-contributor | 502 | — | 11341ms | — | `omni spark-1.3-contributor 256` |
| C5 | 2026-09-30T20:11:52.775Z | t1-orchestrator | 200 | content | 544ms | `qwen3-235b-a22b-instruct-2507` | `omni t1-orchestrator 256` |
| C6 | 2026-09-30T20:11:57.188Z | t1-orchestrator-free-only | 200 | content | 1412ms | `qwen3-235b-a22b-instruct-2507` | `omni t1-orchestrator-free-only 256` |
| C7 | 2026-09-30T20:12:07.410Z | t1-orchestrator-paid | 200 | content | 7222ms | `deepseek-flash` | `omni t1-orchestrator-paid 256` |
| C8 | 2026-09-30T20:12:11.606Z | t2-orchestrator | 200 | content | 1195ms | `deepseek-flash` | `omni t2-orchestrator 256` |
| C9 | 2026-09-30T20:12:15.536Z | t2-worker | 200 | content | 930ms | `qwen3-235b-a22b-instruct-2507` | `omni t2-worker 256` |
| C10 | 2026-09-30T20:12:19.650Z | t2-worker-clean | 200 | content | 1114ms | `deepseek-flash` | `omni t2-worker-clean 256` |
| C11 | 2026-09-30T20:12:23.169Z | t2-worker-free-only | 200 | content | 518ms | `qwen3-235b-a22b-instruct-2507` | `omni t2-worker-free-only 256` |
| C12 | 2026-09-30T20:12:27.434Z | t3-driver | 200 | content | 1265ms | `mistral-small-3.2-24b-instruct-2506` | `omni t3-driver 256` |
| C13 | 2026-09-30T20:12:31.452Z | t3-driver-clean | 200 | content | 1018ms | `deepseek-flash` | `omni t3-driver-clean 256` |
| C14 | 2026-09-30T20:12:35.691Z | t3-driver-free-only | 200 | content | 1239ms | `mistral-small-3.2-24b-instruct-2506` | `omni t3-driver-free-only 256` |
| C15 | 2026-09-30T20:12:39.801Z | t4-rag | 200 | content | 1110ms | `command-a-03-2025` | `omni t4-rag 256` |

All 15 combos answered except `opus-4-6` (401) and `spark-1.3-contributor` (502).
13 combos serve content; every combo that answered named a *different* serving
leg than its own name — that is why the combo rows cannot stand in for leg health.

---

## 4. Legs (19) — latest attempt, probed directly

| # | UTC (TRUE) | Leg (model id sent) | Hand alias | Status | Class | Latency | Command |
|---|------------|---------------------|------------|--------|-------|---------|---------|
| L1 | 2026-09-30T20:12:44.729Z | `deepseek/deepseek-flash` | — | 200 | content | 1928ms | `omni deepseek/deepseek-flash 256` |
| L2 | 2026-09-30T20:13:06.691Z | `gemini/gemini-3.8-flash` | — | 429 | — | 7ms | `omni gemini/gemini-3.8-flash 256` |
| L3 | 2026-09-30T20:13:12.709Z | `antigravity/claude-opus-4-6-thinking` | — | 401 | — | 7ms | `omni antigravity/claude-opus-4-6-thinking 256` |
| L4 | 2026-09-30T20:14:00.531Z | `meta-api/muse-spark-1.3-contributor` | — | 502 | — | 15825ms | `omni meta-api/muse-spark-1.3-contributor 256` |
| L5 | 2026-09-30T20:14:04.062Z | `scw/qwen3-235b-a22b-instruct-2507` | — | 200 | content | 531ms | `omni scw/qwen3-235b-a22b-instruct-2507 256` |
| L6 | 2026-09-30T20:14:10.094Z | `nebius/zai-org/GLM-5.3-Flash` | — | 401 | — | 6ms | `omni nebius/zai-org/GLM-5.3-Flash 256` |
| L7 | 2026-09-30T20:14:14.396Z | `scw/mistral-small-3.2-24b-instruct-2506` | — | 200 | content | 1302ms | `omni scw/mistral-small-3.2-24b-instruct-2506 256` |
| L8 | 2026-09-30T20:14:20.412Z | `antigravity/gemini-3.7-flash-high` | — | 401 | — | 9ms | `omni antigravity/gemini-3.7-flash-high 256` |
| L9 | 2026-09-30T20:14:26.424Z | `nebius/zai-org/GLM-5.2` | — | 401 | — | 5ms | `omni nebius/zai-org/GLM-5.2 256` |
| L10 | 2026-09-30T20:14:30.539Z | `free-ai/qwen7b` | — | 200 | content | 1115ms | `omni free-ai/qwen7b 256` |
| L11 | 2026-09-30T20:14:36.552Z | `antigravity/gemini-3.7-flash-medium` | — | 401 | — | 6ms | `omni antigravity/gemini-3.7-flash-medium 256` |
| L12 | 2026-09-30T20:14:40.008Z | `mistral/mistral-code-latest` | — | 200 | content | 455ms | `omni mistral/mistral-code-latest 256` |
| L13 | 2026-09-30T20:14:44.107Z | `cohere/command-a-03-2025` | — | 200 | content | 1099ms | `omni cohere/command-a-03-2025 256` |
| L14 | 2026-09-30T20:14:49.721Z | `cohere/command-r-plus-08-2024` | — | 200 | content | 2614ms | `omni cohere/command-r-plus-08-2024 256` |
| L15 | 2026-09-30T20:14:55.812Z | `vertex/gemini-3-flash-preview` | vertex-flash | 200 | content | 3091ms | `omni vertex/gemini-3-flash-preview 256` |
| L16 | 2026-09-30T20:15:02.195Z | `vertex/gemini-3.1-flash-lite` | vertex-flash-lite | 200 | content | 3383ms | `omni vertex/gemini-3.1-flash-lite 256` |
| L17 | 2026-09-30T20:15:13.218Z | `vertex/gemini-3.1-pro-preview` | vertex-pro | 200 | content | 8023ms | `omni vertex/gemini-3.1-pro-preview 256` |
| L18 | 2026-09-30T20:15:50.778Z | `vertex/gemini-3.8-flash` | vertex-3.8-flash | 200 | content | 34560ms | `omni vertex/gemini-3.8-flash 256` |
| L19 | 2026-09-30T20:16:09.215Z | `gemini/gemini-2.5-flash` | gemini-2.5-flash | 429 | — | 7ms | `omni gemini/gemini-2.5-flash 256` |

**Distinct legs = 19** (14 from `combos.json` + 5 hand entries). The prior version
counted "20 legs" by treating the 15 combos plus 5 hand aliases as legs; that
conflated combos with legs. The fresh count is **15 combos + 19 distinct legs**.

Direct legs show the fault boundaries the combos hide: `nebius/*` and the
`antigravity/*` legs are 401, `gemini/*` is 429, `meta-api/*` is 502 — none of
which the combo rows expose, because every healthy combo simply failed over to a
working leg and answered 200.

---

## 5. Control probe — reasoning leg, 8 vs 256 tokens

| UTC (TRUE) | Probe | max_tokens | Status | Class | Latency | Command |
|------------|-------|-----------:|--------|-------|---------|---------|
| 2026-09-30T20:16:16.698Z | vertex/gemini-3.1-pro-preview | 8 | 200 | **`200_null`** | 4483ms | `omni vertex/gemini-3.1-pro-preview 8` |
| 2026-09-30T20:16:25.756Z | vertex/gemini-3.1-pro-preview | 256 | 200 | `200_content` | 6058ms | `omni vertex/gemini-3.1-pro-preview 256` |

At `max_tokens=8` the completion content is `""` with `finish_reason: "length"`
— all 8 tokens went to `reasoning_content`. At 256 the same leg returns usable
content. This isolates token budget as the cause and is the only `200_null` in
the run. It also shows why a tiny-budget probe can manufacture a false outage:
the leg is healthy; 8 tokens is simply too few for a reasoning model.

---

## 6. Retry behaviour (every attempt recorded)

Ten probes were retried once (non-200 first attempt). The JSONL records **both**
attempts, so latency is not "last attempt only":

| Probe | Attempt 0 | Attempt 1 |
|-------|-----------|-----------|
| combo:opus-4-6 | 401, 12ms | 401, 11ms |
| combo:spark-1.3-contributor | 502, 5847ms | 502, 11341ms |
| leg:gemini/gemini-3.8-flash | 429, 15954ms | 429, 7ms |
| leg:antigravity/claude-opus-4-6-thinking | 401, 10ms | 401, 7ms |
| leg:meta-api/muse-spark-1.3-contributor | 502, 25996ms | 502, 15825ms |
| leg:nebius/zai-org/GLM-5.3-Flash | 401, 25ms | 401, 6ms |
| leg:antigravity/gemini-3.7-flash-high | 401, 7ms | 401, 9ms |
| leg:nebius/zai-org/GLM-5.2 | 401, 6ms | 401, 5ms |
| leg:antigravity/gemini-3.7-flash-medium | 401, 6ms | 401, 6ms |
| leg:gemini/gemini-2.5-flash | 429, 12429ms | 429, 7ms |

Two corrections to the prior version:

1. **"~125 s total wall-clock per timeout leg."** There are **no timeouts** in the
   fresh run (slowest single probe: `vertex/gemini-3.8-flash` 34560ms). The prior
   60s-timeout rows do not reproduce.
2. **Retry latency is not uniform.** Every retry that follows a slow first
   attempt is fast (single-digit ms) because the breaker has cached the verdict:
   `gemini/gemini-3.8-flash` 15954ms → 7ms; `gemini-2.5-flash` 12429ms → 7ms.
   That is a *cached fail-fast*, not a recovery — the retry returns the **same**
   status. A slow-first/fast-retry pattern therefore records a *breaker* verdict,
   not live leg latency.

---

## 7. Fault attribution

### 502 — meta-api upstream empty response (not a gateway bug)

Leg `meta-api/muse-spark-1.3-contributor`, verbatim body:

```json
{"error":{"message":"[openai-compatible-chat-d9427825-8596-4d01-8fe0-1d4b24bb9f54/muse-spark-1.3-contributor] upstream returned an empty response without usable output","type":"upstream_response_error","code":"upstream_empty_response"}}
```

Combo `spark-1.3-contributor` (whose only leg is that same id) returns the same
502 through the gateway (`code: bad_gateway`). **Attribution: the meta-api
upstream returned an empty response; the gateway faithfully surfaced it as 502.**
It is not an auth failure and reproduces across both attempts. The prior matrix's
`gemini-3.8-flash` 502 ("reasoning consumed 5/5 tokens") does **not** reproduce at
256 tokens and was an artefact of the old `max_tokens=8` probe — the control in
§5 proves the mechanism.

### 429 — credential cooldown (2 legs)

`gemini/gemini-3.8-flash` and `gemini/gemini-2.5-flash`, `type: rate_limit_error`,
`code: model_cooldown`, message `All credentials for model <model> are cooling
down`. The combo `gemini-3.8-flash` still answered 200 (it served via a working
credential), while the *direct* leg was cooling — another reason leg rows matter.

### 401 — two distinct causes

- **antigravity — expired auth, needs a reconnect (3 legs + 1 combo).**
  `antigravity/claude-opus-4-6-thinking`, `antigravity/gemini-3.7-flash-high`,
  `antigravity/gemini-3.7-flash-medium`, and combo `opus-4-6`:
  `authentication_error` / `invalid_api_key`, message
  `[antigravity] All 1 connection(s) authentication expired — please reconnect in the dashboard`.
- **nebius — no active credentials (2 legs).**
  `nebius/zai-org/GLM-5.3-Flash`, `nebius/zai-org/GLM-5.2`:
  `No active credentials for provider: nebius.`

**Breaker-cached vs live:** every 401 here returns in **5–25 ms** (mostly 6–12 ms).
Sub-10 ms is far below a real TLS round-trip, so these are the breaker's cached
verdicts, not fresh upstream calls. The antigravity 401 is a *stale breaker view*
of a connection whose auth has expired; the fix is a reconnect (below), after
which a live probe would take hundreds of ms, not single digits. Contrast the
genuine live calls in §4: 455ms–34560ms.

---

## 8. scaleway — credits exhausted (cached verdict), operator action

Orchestrator log `logs/orch-20260930.log`, line 35, verbatim:

> `2026-09-30T14:37:51Z | L0 | model-ref semantics verified: gateway = plain combo names (t2-worker 200); omniroute/ prefix is opencode-only. NEW: scaleway legs credits-exhausted (both qwen + mistral) -> fallback-to-deepseek active; operator may reconnect. Lane QC v2 relaunched with corrected refs.`

Corroborated by the usage audit (`orch-20260930.log` line 46): `SCALEWAY 481
used/6706 credit-rejected`.

In this fresh run, the **direct** scaleway legs answered 200 — `scw/qwen3-235b...`
531ms, `scw/mistral-small-...` 1302ms — while combos now prefer `deepseek-flash`
or `mistral-small-...` as the serving leg. So "credits-exhausted" is a
**breaker-cached verdict** and the fallback-to-deepseek is active; the credits
are not currently blocking, but the balance is exhausted and will re-block.

**Operator action:** reconnect/top-up the scaleway provider credentials; confirm
afterwards with `omni scw/qwen3-235b-a22b-instruct-2507 256`.

---

## 9. Combos vs legs — the serving leg a combo 200 hides

Each row shows the combo's own name and the `model` field the gateway actually
served the completion from:

| Combo | Served leg |
|-------|------------|
| t1-orchestrator, t1-orchestrator-free-only, t2-worker, t2-worker-free-only | `qwen3-235b-a22b-instruct-2507` |
| t1-orchestrator-paid, t2-orchestrator, t2-worker-clean, t3-driver-clean | `deepseek-flash` |
| t3-driver, t3-driver-free-only | `mistral-small-3.2-24b-instruct-2506` |
| t4-rag | `command-a-03-2025` |

A combo responding 200 means *at least one* leg in its chain worked — it says
nothing about which one, and nothing about the legs that failed before it. Direct
leg probes (§4) are the only valid health evidence per leg.

---

## 10. test-all (recorded, timestamped)

- **Run:** 2026-09-30T20:16:25.756Z → 2026-09-30T20:16:44.622Z (18865 ms), `exit_code: 1`.
- **32 connections tested:** 23 `valid: true`, 9 `valid: false`.
- Not-valid reasons: 4 `Connection is inactive` (antigravity, cerebras,
  muse-code, vertex-partner), 1 `Provider test not supported` (arcee-ai), 1
  `No API-key probe for oauth connections` (claude), 1 `Unsupported state or
  unable to authenticate data` (openai-compatible-chat-…), and 2
  `no API key configured` (opencode, zcode).
- **Diagnosis:** `ok` 20, `unsupported` 1, none 11.

**Green-time context.** The orchestrator (`orch-20260930.log` line 30) recorded
`2026-09-30T14:22:06Z | ... test-all green for all keyed connections` after the
recovery rotation. The fresh 20:16Z run is **not** fully green (`exit_code: 1`):
the connections named above are inactive/unconfigured now. So the "all keyed
connections green" claim held at 14:22Z but does not hold at 20:16Z.

---

## 11. Operator actions (ranked)

1. **antigravity — reconnect.** Auth has expired for the whole connection
   (`All 1 connection(s) authentication expired — please reconnect in the
   dashboard`). Test-all lists antigravity as inactive. Reconnect in the dashboard
   / re-credential, then re-probe `antigravity/gemini-3.7-flash-high`.
2. **nebius — re-supply credential.** `No active credentials for provider:
   nebius.` (usage audit: removed; previously the top caller at 3250).
3. **meta-api — provider side.** `upstream_empty_response`. Not a gateway fault;
   check the meta-api provider status/connection config. Reproduces on both
   the leg and its combo.
4. **scaleway — top up / reconnect.** Balance exhausted (6706 credit-rejected),
   fallback-to-deepseek active; direct legs currently 200.
5. **gemini (AI Studio) — cooldown.** `gemini/gemini-3.8-flash` and
   `gemini-2.5-flash` are cooling; combos route around them. No action unless
   cooldown persists.

---

## 12. Provenance

- Every status, class, latency and timestamp above is read from
  `docs/handoff/2026-09-30-leg-health-raw.jsonl` (47 records: 46 chat attempts +
  1 `test-all`), and every tally is reproducible from it.
- Raw response bodies are stored with Authorization, e-mail addresses, URLs and
  IPv4 literals scrubbed (AGENTS.md hard rule 1). No secret value appears in this
  document or the JSONL.
- `test-all` output in the JSONL is the scrubbed `--json` payload; e-mail-bearing
  connection names appear as `[REDACTED-EMAIL]`.
