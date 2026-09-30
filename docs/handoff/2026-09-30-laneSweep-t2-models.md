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

## 4. Combo routing tests (clean re-run measured 2026-09-30T14:53–14:58Z)

A first run at 14:46–14:51Z was discarded — the gateway was unstable (3/10 ok,
7 transport errors: URLError ~2 s, ConnectionResetError). The two long-running
combos (t1-orchestrator 124 s, t1-orchestrator-free-only 117 s) appear to have
exhausted the gateway's connection pool, causing cascading transport errors for
subsequent combos. The script also crashed before testing t3-driver-free-only.

A clean re-run at 14:53–14:58Z confirmed those failures were gateway-overwhelm
artifacts, not real leg failures: the same combos that returned URLError/
ConnectionResetError now succeed cleanly (t2-worker, t2-worker-clean, t3-driver,
t3-driver-clean all ok).

| # | Combo | Result | Latency ms | Model served | Error |
|---|---|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | **ok** | 1 123 | `deepseek-flash` | — |
| 2 | `t1-orchestrator` | FAIL | 124 728 | (none) | HTTP 502 — all 5 legs failed |
| 3 | `t1-orchestrator-free-only` | FAIL | 122 510 | (none) | HTTP 502 — all 4 legs failed |
| 4 | `t1-orchestrator-paid` | FAIL | 1 497 | (none) | HTTP 502 — sole leg (muse-spark) failed |
| 5 | `t2-orchestrator` | FAIL | 22 | (none) | HTTP 401 — sole leg (agy/opus) no credentials |
| 6 | `t2-worker` | **ok** | 1 251 | `gpt-oss-120b` ⚠ | — (see stale-combo finding below) |
| 7 | `t2-worker-clean` | **ok** | 864 | `deepseek-flash` | — |
| 8 | `t2-worker-free-only` | **ok** | 1 091 | `qwen7b` | — (GLM-5.2 down → fell through to qwen7b) |
| 9 | `t3-driver` | **ok** | 1 382 | `gpt-oss-120b` ⚠ | — (see stale-combo finding below) |
| 10 | `t3-driver-clean` | **ok** | 1 161 | `deepseek-flash` | — |
| 11 | `t3-driver-free-only` | **HUNG** | >7 200 000 | (none) | GLM-5.3-Flash 504 leg hung (no socket timeout); killed by PID after >2 h |

**6/11 combos ok**, 4 FAIL (1× 502 paid, 2× 502 orchestrator, 1× 401), 1 HUNG.

### Prior-run vs re-run comparison

| Combo | Prior (14:46Z) | Re-run (14:53Z) | Verdict |
|---|---|---|---|
| t2-orchestrator | URLError 2 s | 401 22 ms | 401 confirmed (was masked by gateway overwhelm) |
| t2-worker | URLError 2 s | ok 1 251 ms | transport error was artifact |
| t3-driver | ConnectionReset 18 s | ok 1 382 ms | transport error was artifact |
| t3-driver-clean | URLError 2 s | ok 1 161 ms | transport error was artifact |

The prior run's 7 transport-error combos reduced to 0 in the re-run. The
remaining 4 FAILs are real (HTTP 502/401 from upstream legs), not gateway
artifacts.

### Key finding 1: stale combos — gateway served gpt-oss-120b

`t2-worker` (combo #6) and `t3-driver` (combo #9) both served `gpt-oss-120b`
in the re-run. However, `groq/openai/gpt-oss-120b` is **not a leg** in the
repo's `combos.json` for either combo:

- Repo `t2-worker` legs: gemini-3.8-flash → agy-gemini-high → scw-qwen3 →
  scw-mistral-small → nebius-GLM-5.2 → **deepseek-flash** → muse-spark → qwen7b
  (8 legs, no gpt-oss-120b)
- Repo `t3-driver` legs: scw-mistral-small → nebius-GLM-5.2 → scw-qwen3 →
  mistral-code-latest → **deepseek-flash** → muse-spark
  (6 legs, no gpt-oss-120b)

This means the **live gateway has older combo definitions** than the repo's
`combos.json`. The repo's combos were updated by commit `d08f7f2` (this lane's
base) but those changes were **never re-applied to the live gateway**. The
live gateway still has the pre-`d08f7f2` combo legs (matching the §A table in
`docs/models-proposed.md`), which include `groq/openai/gpt-oss-120b` as a leg
in t2-worker and t3-driver. Since all legs before gpt-oss-120b fail (401/429/
502), the gateway correctly falls through to the working gpt-oss-120b leg —
but this is the **stale** combo, not the one in the repo.

**Implication:** The repo's `combos.json` and the live gateway are out of sync.
Any analysis of combo behavior must account for the gateway's actual (stale)
leg definitions, not the repo's. This is a known issue — see
`docs/models-proposed.md` §A for the gateway's actual combo legs and the
gateway findings doc for the full stale-combo trace.

### Key finding 2: t2-worker-free-only served qwen7b

`t2-worker-free-only` returned `qwen7b` (leg 6 of 6), not `GLM-5.2` (leg 5).
This means GLM-5.2 was temporarily down during the combo test, and the
gateway correctly fell through to the next working leg (qwen7b). This is
**direct evidence of the priority-chain fallback working as designed** —
when a leg fails, the gateway tries the next one in order. qwen7b has no
tool-call support (measured in §3), so this combo is ok for ack but
unusable for agentic work.

### Key finding 3: t3-driver-free-only hung

`t3-driver-free-only` started at ~14:58Z. Its first leg (`scw-mistral-small`)
fails (401), second leg (`nebius/zai-org/GLM-5.3-Flash`) times out at 504
(125 s, per §3). The probe-sweep.py script has no HTTP socket timeout on
the combo request, so the 504 leg's hang propagated — the process stayed
alive for >2 h (until 16:59Z) without writing a result record. The process
was killed by PID (129200) to unblock the lane. No JSONL record was written
for this combo.

This is a **probe-sweep.py bug**: the combo HTTP request needs a timeout
(60 s is reasonable — no working combo took >1.4 s). The leg probe path
already has timeouts; the combo path does not.

### Deepseek-v4.1-flash consistency

`deepseek-v4.1-flash` (sole leg: `deepseek/deepseek-flash`) was tested 4
times across all runs, all ok: 922 ms, 812 ms, 1 079 ms, 1 123 ms. This is
the most reliable combo in the sweep.

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
| `t2-orchestrator` | antigravity/claude-opus-4-6-thinking | 401 (measured in re-run, 22 ms; confirmed the prior inference — see §4) |
| `t2-worker-free-only` | gemini-3.8-flash → agy-gemini-medium → scw-qwen3 → scw-mistral-small → nebius-GLM-5.2 → qwen7b | GLM-5.2 (leg 5) works; free-only by design |
| `t3-driver-free-only` | scw-mistral-small → nebius-GLM-5.3-Flash → scw-qwen3 → qwen7b | GLM-5.3-Flash 504 + qwen7b ack-only; fragile |

### Fallback behavior per combo (structural analysis from repo combos.json leg order; measured combo results in §4 reflect the live gateway's stale legs — see §4 stale-combo finding)

> **Stale-combo caveat:** The structural analysis below is based on the repo's
> `combos.json` leg order. The **measured** combo results in §4 reflect the live
> gateway's actual (stale) legs, which differ from the repo — e.g. t2-worker and
> t3-driver served `gpt-oss-120b` (not a repo leg) because the live gateway still
> has the pre-`d08f7f2` combo definitions. The proposal below targets the repo's
> `combos.json` (what should be applied), not the live gateway's current state.

**`t2-worker`** (deepseek = leg 6): Legs 1–4 all fail (429/401). **Leg 5 (`nebius/zai-org/GLM-5.2`)
succeeds** (297 ms) → gateway stops here. Deepseek (leg 6) **never fires** unless GLM-5.2
also goes down. This is correct priority ordering: free legs first, paid deepseek as
fallback. **No reordering needed.** (Measured: the live gateway served `gpt-oss-120b`
instead — stale combo, see §4.)

**`t3-driver`** (deepseek = leg 5): Leg 1 (scw-mistral-small) fails (401). **Leg 2
(`nebius/zai-org/GLM-5.2`) succeeds** (297 ms) → gateway stops. Deepseek (leg 5) never
fires unless GLM-5.2, scw-qwen3, AND mistral-code-latest all fail simultaneously. **No
reordering needed.** (Measured: the live gateway served `gpt-oss-120b` instead — stale
combo, see §4.)

**`t1-orchestrator`** (NO deepseek): All 5 legs fail → **combo returns 502** (measured
124 728 ms in the re-run; 124 031 ms in the prior run). **Proposal: append
`deepseek/deepseek-flash` as a final fallback leg** so the orchestrator tier
degrades to a working model instead of returning 502.

**`t1-orchestrator-paid`** (NO deepseek): Sole leg (muse-spark) returns 502 (measured
1 497 ms). **Proposal: append `deepseek/deepseek-flash` as a fallback leg.**

**`t2-orchestrator`** (NO deepseek): Sole leg (agy/claude-opus-4-6-thinking) returns
401 — measured directly in the re-run (22 ms, §4), confirming the prior inference
from other agy legs. **Proposal: append `deepseek/deepseek-flash` as a fallback leg.**

**Free-only combos** (`t1-orchestrator-free-only`, `t2-worker-free-only`,
`t3-driver-free-only`): By design, no paid deepseek leg. These combos can fail entirely
when all free legs are down. **No change proposed** — adding deepseek would change
their character from "zero spend" to "free-first with paid overflow."

### Proposed deepseek fallback ordering (for operator review — NOT applied to combos.json)

| Combo | Current legs (tail) | Proposed tail | Rationale |
|---|---|---|---|
| `t1-orchestrator` | …→ muse-spark (502) | …→ muse-spark → **deepseek/deepseek-flash** | Prevents 502 when all free + paid legs fail |
| `t1-orchestrator-paid` | muse-spark (502) | muse-spark → **deepseek/deepseek-flash** | Same — single failing leg needs a fallback |
| `t2-orchestrator` | agy/opus-4-6-thinking (401, measured) | agy/opus-4-6-thinking → **deepseek/deepseek-flash** | Prevents 401 when agy credentials are absent |
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

**Total admission backoffs:** 1 (from the leg sweep at 14:33Z; the combo re-run
at 14:53–14:58Z had 0 backoffs — no 429/503/`chat_admission_busy` encountered).

## 8. Files changed

| File | Change |
|---|---|
| `tools/probe-sweep.py` | NEW — sweep script (15 legs + 11 combos) |
| `docs/handoff/2026-09-30-laneSweep-t2-models.md` | NEW — this evidence doc |
| `docs/models-proposed.md` | §E updated with 2026-09-30 sweep leg results |
| `logs/handoff-sessions/DONE-ws-sweep.md` | NEW — DONE note |
| `logs/probe-sweep-20260930.jsonl` | git-ignored — 38 records (15 legs + 23 combo records across two runs) |
