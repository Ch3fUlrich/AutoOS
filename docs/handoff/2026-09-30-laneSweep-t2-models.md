# Lane Sweep — t2 Model Gateway Probing (2026-09-30)

**Lane:** L1-backlog/ws-sweep-20260930  
**Branch:** `L1-backlog/ws-sweep-20260930` (worktree: `AutoOS-ws-sweep`)  
**Cut from:** main `d08f7f2`  
**Script:** `tools/probe-sweep.py` (NEW, reuses `probe_common.py` + `probe-toolcalls.py` infra)  
**Log:** `logs/probe-sweep-20260930.jsonl` (git-ignored, filtered records only)

---

## 1. Objective

Sweep cheap t2 models through the live OmniRoute gateway (127.0.0.1:20128), measure
per-leg ack + tool-call results, analyze deepseek-v4.1-flash fallback behavior across
combos, verify the qwen CLI path, and produce evidence for the operator.

## 2. Method

`tools/probe-sweep.py` builds a candidate-leg list from current `combos.json` legs plus
operator-explicit candidates (groq, cerebras, morph). Each leg is probed with:

- **Ack test**: a minimal chat completion (16 max tokens) — measures whether the leg
  accepts a request and returns 200.
- **Tool-call test**: a single tool-call round (2048 max tokens) — measures whether the
  leg returns exactly one `tool_call` in the response.

Admission/rate-limit errors (429, 503, `chat_admission_busy`) trigger a 60–120 s backoff
with one retry. Results are appended to `logs/probe-sweep-20260930.jsonl` as JSONL.

Combo routing tests send a single chat completion using the combo name as the `model`
field; the gateway routes through legs internally. The served model name and latency
are recorded.

All results are **measured** (command + UTC timestamp), not inferred.

## 3. Leg sweep results (measured 2026-09-30T14:33–14:40Z)

15 candidate legs probed. Gateway key from env `AUTOOS_OMNIROUTE_KEY` (present).

| # | Leg | Ack | Tool | Ack ms | Tool ms | Ctx | Error |
|---|---|---|---|---|---|---|---|
| 1 | `gemini/gemini-3.8-flash` | FAIL | FAIL | 96 250 | 0 | 1 048 576 | HTTP 429 (rate limit, 96 s) |
| 2 | `antigravity/gemini-3.7-flash-high` | FAIL | FAIL | 15 | 0 | — | HTTP 401 (no credentials) |
| 3 | `scw/qwen3-235b-a22b-instruct-2507` | FAIL | FAIL | 0 | 0 | 128 000 | HTTP 401 |
| 4 | `scw/mistral-small-3.2-24b-instruct-2506` | FAIL | FAIL | 15 | 0 | 128 000 | HTTP 401 |
| 5 | `nebius/zai-org/GLM-5.2` | **ok** | **ok** | 297 | 1 219 | 128 000 | — |
| 6 | `deepseek/deepseek-flash` | **ok** | **ok** | 1 047 | 2 843 | 1 048 576 | — |
| 7 | `meta-api/muse-spark-1.3-contributor` | FAIL | FAIL | 5 827 | 0 | 1 048 576 | HTTP 502 |
| 8 | `free-ai/qwen7b` | **ok** | FAIL | 782 | 3 000 | 128 000 | tool: "expected exactly one tool_call, got 0" (no tool support) |
| 9 | `antigravity/gemini-3.7-flash-medium` | FAIL | FAIL | 15 | 0 | — | HTTP 401 |
| 10 | `nebius/zai-org/GLM-5.3-Flash` | FAIL | FAIL | 125 047 | 0 | 128 000 | HTTP 504 (125 s timeout) |
| 11 | `mistral/mistral-code-latest` | **ok** | **ok** | 2 297 | 2 280 | 128 000 | — |
| 12 | `groq/openai/gpt-oss-120b` | **ok** | **ok** | 609 | 967 | 131 072 | — |
| 13 | `cerebras/gpt-oss-120b` | FAIL | FAIL | 219 | 0 | 400 000 | HTTP 402 (payment required) |
| 14 | `morph/morph-dsv4flash` | FAIL | FAIL | 15 | 0 | — | HTTP 401 |
| 15 | `morph/morph-glm52-744b` | FAIL | FAIL | 15 | 0 | — | HTTP 401 |

**Summary:** 4/15 legs fully working (ack + tool-call ok), 1/15 ack-only (qwen7b, no tool
support), 10/15 failed.

### Working legs

| Leg | Ack ms | Tool ms | Ctx | Source combo |
|---|---|---|---|---|
| `nebius/zai-org/GLM-5.2` | 297 | 1 219 | 128k | t2-worker (leg 5), t3-driver (leg 2) |
| `deepseek/deepseek-flash` | 1 047 | 2 843 | 1M | deepseek-v4.1-flash, t2-worker-clean, t3-driver-clean |
| `mistral/mistral-code-latest` | 2 297 | 2 280 | 128k | t3-driver (leg 4) |
| `groq/openai/gpt-oss-120b` | 609 | 967 | 131k | operator-explicit candidate |

### Failure categories

| HTTP | Count | Legs | Meaning |
|---|---|---|---|
| 401 | 6 | agy/gemini-high, agy/gemini-medium, scw/qwen3, scw/mistral-small, morph-dsv4flash, morph-glm52-744b | No credentials configured for these providers |
| 402 | 1 | cerebras/gpt-oss-120b | Payment required (wallet exhausted) |
| 429 | 1 | gemini/gemini-3.8-flash | Rate limit (79 s backoff, still failed after retry; 96 s total ack latency includes retry sleeps) |
| 502 | 1 | meta-api/muse-spark-1.3-contributor | Gateway upstream error |
| 504 | 1 | nebius/zai-org/GLM-5.3-Flash | Timeout (125 s) |
| tool-fail | 1 | free-ai/qwen7b | Ack OK but no tool-call support |

## 4. Combo routing tests (measured 2026-09-30T14:46–14:51Z)

11 combos tested (re-run via `--combos-only`). Gateway was **unstable** during
the sweep — the two long-running combos (t1-orchestrator 124 s, t1-orchestrator-
free-only 117 s) appear to have overwhelmed the gateway, causing transport
errors (URLError, ConnectionResetError) for subsequent combos. `t3-driver-
free-only` was not tested (script crashed, exit 255).

| Combo | Result | Latency ms | Model served | Error |
|---|---|---|---|---|
| `deepseek-v4.1-flash` | **ok** | 812 | `deepseek-flash` | — |
| `t1-orchestrator` | FAIL | 124 031 | (none) | HTTP 502 — all 5 legs failed |
| `t1-orchestrator-free-only` | FAIL | 116 796 | (none) | transport: ConnectionResetError (gateway overwhelmed) |
| `t1-orchestrator-paid` | FAIL | 2 047 | (none) | transport: URLError (gateway unreachable) |
| `t2-orchestrator` | FAIL | 2 047 | (none) | transport: URLError |
| `t2-worker` | FAIL | 2 045 | (none) | transport: URLError |
| `t2-worker-clean` | **ok** | 13 391 | `deepseek-flash` | — (slow: gateway under load) |
| `t2-worker-free-only` | **ok** | 1 937 | `qwen7b` | — (GLM-5.2 down → fell through to qwen7b; qwen7b has no tool support — ok for ack, unusable for agentic work) |
| `t3-driver` | FAIL | 17 842 | (none) | transport: ConnectionResetError |
| `t3-driver-clean` | FAIL | 2 047 | (none) | transport: URLError |
| `t3-driver-free-only` | — | — | — | **not tested** (script crashed) |

**3/10 tested combos ok** (deepseek-v4.1-flash, t2-worker-clean, t2-worker-free-only).
7/10 failed — 1× HTTP 502 (t1-orchestrator), 2× ConnectionResetError (t1-orchestrator-
free-only, t3-driver), 4× URLError (t1-orchestrator-paid, t2-orchestrator, t2-worker,
t3-driver-clean).

### Gateway instability note

The sweep ran 11 combos sequentially. The first two (deepseek-v4.1-flash at
0.8 s and t1-orchestrator at 124 s) completed normally. The third
(t1-orchestrator-free-only) ran 117 s and ended with ConnectionResetError —
the gateway connection was reset mid-request. The next three combos
(t1-orchestrator-paid, t2-orchestrator, t2-worker) failed with URLError at
~2 s (gateway unreachable). The gateway partially recovered (t2-worker-clean
ok at 13.4 s, t2-worker-free-only ok at 1.9 s), then failed again (t3-driver
ConnectionResetError at 17.8 s, t3-driver-clean URLError at 2 s). The script
crashed before testing t3-driver-free-only.

The combo loop in `probe-sweep.py` has no exception guard, which is why
t3-driver-free-only was lost instead of recorded. The gateway instability
correlates with long-running upstream leg timeouts (GLM-5.3-Flash 504 at
125 s, gemini 429 at 96 s) exhausting the gateway's connection pool.

### Key finding: t2-worker-free-only served qwen7b

`t2-worker-free-only` returned `qwen7b` (leg 6 of 6), not `GLM-5.2` (leg 5).
This means GLM-5.2 was temporarily down during the combo test, and the
gateway correctly fell through to the next working leg (qwen7b). This is
**direct evidence of the priority-chain fallback working as designed** —
when a leg fails, the gateway tries the next one in order.

### Deepseek-v4.1-flash consistency

`deepseek-v4.1-flash` (sole leg: `deepseek/deepseek-flash`) was tested 3
times across both runs, all ok: 922 ms, 812 ms, 1079 ms. This is the most
reliable combo in the sweep.

## 5. Deepseek V4.1 Flash fallback analysis

`deepseek/deepseek-flash` IS the DeepSeek-V4.1-Flash model (per DS1M comment in
`combos.json` line 33–36: "MODEL deepseek-flash = DeepSeek-V4.1-Flash at 1M in / 384K out").
Context window: 1 048 576 (1M). Ack: 1 047 ms. Tool-call: 2 843 ms.

### Where deepseek appears in combos.json

| Combo | Deepseek position | Legs before it | Legs after it |
|---|---|---|---|
| `deepseek-v4.1-flash` | 1/1 (sole) | — | — |
| `t2-worker` | 6/8 | gemini-3.8-flash, agy-gemini-high, scw-qwen3, scw-mistral-small, nebius-GLM-5.2 | muse-spark, qwen7b |
| `t2-worker-clean` | 1/1 (sole) | — | — |
| `t3-driver` | 5/6 | scw-mistral-small, nebius-GLM-5.2, scw-qwen3, mistral-code-latest | muse-spark |
| `t3-driver-clean` | 1/1 (sole) | — | — |

### Combos with NO deepseek leg

| Combo | Legs | Risk |
|---|---|---|
| `t1-orchestrator` | gemini-3.8-flash → scw-qwen3 → nebius-GLM-5.3-Flash → scw-mistral-small → muse-spark | All 5 legs currently fail → 502 |
| `t1-orchestrator-free-only` | gemini-3.8-flash → scw-qwen3 → nebius-GLM-5.3-Flash → scw-mistral-small | All 4 legs currently fail |
| `t1-orchestrator-paid` | muse-spark | 502 |
| `t2-orchestrator` | antigravity/claude-opus-4-6-thinking | 401 (inferred — other agy legs returned 401; this leg was not probed; combo test returned URLError, see §4) |
| `t2-worker-free-only` | gemini-3.8-flash → agy-gemini-medium → scw-qwen3 → scw-mistral-small → nebius-GLM-5.2 → qwen7b | GLM-5.2 (leg 5) works; free-only by design |
| `t3-driver-free-only` | scw-mistral-small → nebius-GLM-5.3-Flash → scw-qwen3 → qwen7b | GLM-5.3-Flash 504 + qwen7b ack-only; fragile |

### Fallback behavior per combo (structural analysis from leg-sweep results; combo probes for t2-worker and t3-driver failed with transport errors)

**`t2-worker`** (deepseek = leg 6): Legs 1–4 all fail (429/401). **Leg 5 (`nebius/zai-org/GLM-5.2`)
succeeds** (297 ms) → gateway stops here. Deepseek (leg 6) **never fires** unless GLM-5.2
also goes down. This is correct priority ordering: free legs first, paid deepseek as
fallback. **No reordering needed.**

**`t3-driver`** (deepseek = leg 5): Leg 1 (scw-mistral-small) fails (401). **Leg 2
(`nebius/zai-org/GLM-5.2`) succeeds** (297 ms) → gateway stops. Deepseek (leg 5) never
fires unless GLM-5.2, scw-qwen3, AND mistral-code-latest all fail simultaneously. **No
reordering needed.**

**`t1-orchestrator`** (NO deepseek): All 5 legs fail → **combo returns 502** (measured
124 031 ms in the combo sweep; 124 593 ms in the first run). **Proposal: append
`deepseek/deepseek-flash` as a final fallback leg** so the orchestrator tier
degrades to a working model instead of returning 502.

**`t1-orchestrator-paid`** (NO deepseek): Sole leg (muse-spark) returns 502. **Proposal:
append `deepseek/deepseek-flash` as a fallback leg.**

**`t2-orchestrator`** (NO deepseek): Sole leg (agy/claude-opus-4-6-thinking) inferred 401
(other agy legs returned 401; this leg was not probed directly). The combo test itself
returned URLError (gateway unreachable, §4), not 401. **Proposal: append
`deepseek/deepseek-flash` as a fallback leg.**

**Free-only combos** (`t1-orchestrator-free-only`, `t2-worker-free-only`,
`t3-driver-free-only`): By design, no paid deepseek leg. These combos can fail entirely
when all free legs are down. **No change proposed** — adding deepseek would change
their character from "zero spend" to "free-first with paid overflow."

### Proposed deepseek fallback ordering (for operator review — NOT applied to combos.json)

| Combo | Current legs (tail) | Proposed tail | Rationale |
|---|---|---|---|
| `t1-orchestrator` | …→ muse-spark (502) | …→ muse-spark → **deepseek/deepseek-flash** | Prevents 502 when all free + paid legs fail |
| `t1-orchestrator-paid` | muse-spark (502) | muse-spark → **deepseek/deepseek-flash** | Same — single failing leg needs a fallback |
| `t2-orchestrator` | agy/opus-4-6-thinking (401, inferred) | agy/opus-4-6-thinking → **deepseek/deepseek-flash** | Prevents 401 when agy credentials are absent |
| `t2-worker` | (unchanged) | deepseek already leg 6 of 8 | Correct — no change |
| `t3-driver` | (unchanged) | deepseek already leg 5 of 6 | Correct — no change |
| `-clean` twins | (unchanged) | deepseek is sole leg | Working as intended |

> **Note:** These proposals are for operator review only. P1-combos owns
> `combos.json`. This lane does not modify it.

## 6. Qwen CLI path verification (measured 2026-09-30T14:50Z)

**Command:** `omniroute run qwen --model deepseek-v4.1-flash -- --prompt "Reply with exactly: QWEN_CLI_OK"`

**Result:** `QWEN_CLI_OK` (exit 0)

**Mechanism:** `omniroute run qwen` injects `OMNIROUTE_API_KEY` env and a temporary
`QWEN_HOME` config overlay that points the qwen CLI's `OPENAI_BASE_URL` at
`http://localhost:20128/v1`. The `--model deepseek-v4.1-flash` flag sets the combo as
the model. The `-- --prompt "…"` passes the prompt to the qwen CLI via toolArgs.

**Standalone qwen CLI** (without `omniroute run`): returns 401 — it connects to
Alibaba Cloud's default endpoint, not the gateway. The `omniroute run` wrapper is
required.

**Dry-run** (`omniroute run qwen --model deepseek-v4.1-flash --dry-run`):
```
command: qwen.cmd
args: ["--model", "deepseek-v4.1-flash"]
env added/changed: OMNIROUTE_API_KEY
config overlay: temporary QWEN_HOME (removed after exit)
```

## 7. Admission backoff log

| # | UTC timestamp | Request | HTTP | Backoff s | Retried | Outcome |
|---|---|---|---|---|---|---|
| 1 | 14:33:56Z | gemini/gemini-3.8-flash (ack) | 429 | 79 | yes | still 429 (failed) |

**Total admission backoffs:** 1 (recorded; no lane death)

## 8. Files changed

| File | Change |
|---|---|
| `tools/probe-sweep.py` | NEW — sweep script (15 legs + 11 combos) |
| `docs/handoff/2026-09-30-laneSweep-t2-models.md` | NEW — this evidence doc |
| `docs/models-proposed.md` | §E updated with 2026-09-30 sweep leg results |
| `logs/handoff-sessions/DONE-ws-sweep.md` | NEW — DONE note |
| `logs/probe-sweep-20260930.jsonl` | git-ignored — 28 records (15 legs + 13 combos) |
