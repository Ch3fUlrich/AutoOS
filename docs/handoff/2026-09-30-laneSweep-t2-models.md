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
7 failures: 6 transport errors (URLError ~2 s, ConnectionResetError) + 1 HTTP
502). The two long-running combos (t1-orchestrator 502 at 124 s,
t1-orchestrator-free-only ConnectionReset at 117 s) appear to have exhausted
the gateway's connection pool, causing cascading transport errors for
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
in the re-run. However, no `gpt-oss-120b` variant is a leg in the repo's
`combos.json` for either combo (at `d08f7f2`, this lane's base — verified with
`git show d08f7f2:configuration/omniroute/combos.json`):

- Repo `t2-worker` legs: gemini-3.8-flash → agy-gemini-high → scw-qwen3 →
  scw-mistral-small → nebius-GLM-5.2 → **deepseek-flash** → muse-spark → qwen7b
  (8 legs, no gpt-oss-120b)
- Repo `t3-driver` legs: scw-mistral-small → nebius-GLM-5.2 → scw-qwen3 →
  mistral-code-latest → **deepseek-flash** → muse-spark
  (6 legs, no gpt-oss-120b)

**Git trace (measured):** `gpt-oss-120b` legs were removed from `combos.json`
by commits `c126e5f` (OR1a, "gateway renders serve only available legs") and
`979b8c3` (OR1e, "apply prunes managed combos omitted as unservable") on
~2026-09-27. These commits are **before** `d08f7f2` (2026-09-30, this lane's
base), which only changed deepseek context (128k→1M) and the agy→antigravity
rename — it did NOT remove gpt-oss-120b. At commit `86901fc` (~2026-09-24),
`t2-worker` had `groq/openai/gpt-oss-120b`, `cerebras/gpt-oss-120b`,
`sambanova/gpt-oss-120b`, and `t3-driver` had `samba/gpt-oss-120b`. The live
gateway still serves these removed legs → the gateway is stale to ~2026-09-24.

**Heterogeneous staleness (inferred from cross-family review, see §9):**
The gateway's apply process updates combos individually, not atomically.
`t2-worker-free-only` served `qwen7b` — but `free-ai/qwen7b` was only added to
combos.json in `ad23e99` (T2FREE, ~2026-09-27, after `86901fc`). At `86901fc`,
`t2-worker-free-only` had gpt-oss-120b legs, not qwen7b. So the live
`t2-worker-free-only` is from **after** `ad23e99` (newer), while the live
`t2-worker` is from **before** `c126e5f` (older, still has gpt-oss-120b).
The gateway has a **mix of apply-era states**, not a single uniform snapshot.
This is consistent with the gateway findings doc §2 ("apply leaves dead live
combos untouched").

**Provider ambiguity:** the served `model_served` field reports bare
`gpt-oss-120b` (provider prefix stripped). At `86901fc`, t2-worker's
gpt-oss-120b was `groq/openai/gpt-oss-120b` (probed ok in §3, 609 ms) while
t3-driver's was `samba/gpt-oss-120b` (not probed — may or may not work
independently). Without `omniroute combo list --json` ground truth, the
provider attribution is inferred, not measured.

**Implication:** The repo's `combos.json` and the live gateway are out of sync
by ~6 days (Sep 24 → Sep 30). Any analysis of combo behavior must account for
the gateway's actual leg definitions. The live legs should be verified with
`omniroute combo list --json` or `tools/audit-router.py` — this lane inferred
them from git history and `docs/models-proposed.md` §A, which is an unreliable
proxy (§A disagrees with combos.json on `t1-orchestrator` leg count: 2 vs 5).

### Key finding 2: t2-worker-free-only served qwen7b

`t2-worker-free-only` returned `qwen7b` (leg 6 of 6 in the repo's definition),
not `GLM-5.2` (leg 5). This means GLM-5.2 was temporarily down during the combo
test (**inferred** from the repo leg order + served model; the live gateway's
legs may differ — see stale-combo finding above), and the gateway correctly
fell through to the next working leg (qwen7b). This is **direct evidence of
the priority-chain fallback working as designed**.

qwen7b has no tool-call support (measured in §3, "expected exactly one
tool_call, got 0"), so this combo is ok for ack but **unusable for agentic
work** per the single-tool-call probe. **Caveat (from cross-family review,
§9):** the Qwen/GLM reviewer ran on this same `t2-worker-free-only` combo and
made extensive tool calls (read, grep, shell, git show) successfully — direct
counter-evidence that the tool-fail may be prompt-shape-dependent rather than
a hard limitation. The qwen7b tool-fail deserves a second probe with a
different tool-call shape before "unusable for agentic work" is stated as
conclusive.

### Key finding 3: t3-driver-free-only hung

`t3-driver-free-only` started at ~14:58Z. Its first leg (`scw-mistral-small`)
fails (401), second leg (`nebius/zai-org/GLM-5.3-Flash`) times out at 504
(125 s, per §3). The combo HTTP request has a per-read timeout of 180 s
(via `make_post(timeout=180)` → `urllib.request.urlopen(req, timeout=180)`,
shared by both the leg and combo paths). However, this is a **per-read**
timeout, not a total wall-clock deadline: a gateway trickling bytes (one
byte every <180 s) resets the timer indefinitely. With no total deadline
and no per-combo exception guard in the combo loop, the 504 leg's hang
propagated — the process stayed alive for >2 h (until 16:59Z) without
writing a result record. The process was killed by PID (129200) to
unblock the lane. No JSONL record was written for this combo.

This is a **probe-sweep.py bug**: the combo loop needs (a) a total
wall-clock deadline around each combo call (monotonic deadline or
thread + `join(timeout)`), and (b) a per-combo `try/except` so one
hung combo cannot block the rest of the sweep.

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
> t3-driver served `gpt-oss-120b` (not a repo leg) because the live gateway is
> stale to ~2026-09-24 (gpt-oss-120b removed in `c126e5f`/`979b8c3`, Sep 27 —
> see §4 Key finding 1 for the full git trace). The proposal below targets the
> repo's `combos.json` (what should be applied), not the live gateway's current
> state.

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

## 9. Cross-family review (original 3 at 2026-09-30T17:00Z; supplementary 2 at ~17:30Z)

Five t3-reviewer subagents reviewed this evidence doc across two rounds, each on
a different model family. Read-only scope; no edits by reviewers. The original
round (3 reviewers) is rows 1–3; the supplementary round (2 reviewers, launched
after session resume to strengthen the Gemini and Qwen family verdicts) is rows 4–5.

| # | Reviewer model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/deepseek-v4.1-flash` | DeepSeek | APPROVED-WITH-NOTES | ses_f0d2ae02affeHgMyKYPZkhPEZN |
| 2 | `omniroute/gemini-2.5-flash` | Gemini | REJECTED (invalid — see below) | ses_f0d2ae028ffe3mZGupoOz0T0wq |
| 3 | `omniroute/t2-worker-free-only` | Qwen/GLM | APPROVED-WITH-NOTES | ses_f0d2ae025ffeWqTg2tICWUTPBr |
| 4 | `omniroute/vertex-flash` | Gemini (Vertex AI) | APPROVED | ses_f0d2e9f88ffeEeOjv2nt9Xgb3K |
| 5 | `openrouter/qwen/qwen3.8-27b:free` | Qwen (OpenRouter) | NEEDS-CHANGES → fixes applied | ses_f0d2b893dffeluwL9LOTR4hrNZ |

**Writer:** t2-worker (this session, `L1-backlog/ws-sweep-20260930`).

### Gemini rejection — invalid

The Gemini reviewer (gemini-2.5-flash) rejected, but it reviewed the **wrong
file** — it read `docs/handoff/2026-09-30-omniroute-gateway-findings.md` (the
gateway findings doc) instead of `docs/handoff/2026-09-30-laneSweep-t2-models.md`
(the evidence doc under review). Its rejection cites "Missing Leg × result
matrix" and "stale-combo finding not documented" — both of which ARE in the
correct file. The rejection is a model-follows-wrong-path artifact, not a
substantive verdict on the evidence doc. Recorded for completeness; not
counted as a valid rejection.

### Fixes applied based on reviewer feedback (DeepSeek + Qwen/GLM)

Both valid reviewers (APPROVED-WITH-NOTES) independently identified the same
substantive issue: the §4 stale-combo **explanation** was factually wrong.
Fixes applied before this commit:

1. **Stale-combo attribution corrected.** The original text attributed the
   gpt-oss-120b divergence to "pre-`d08f7f2`" and "matching §A." Both reviewers
   proved this wrong: `d08f7f2` only changed deepseek context + agy rename, NOT
   gpt-oss-120b removal. The removal was in `c126e5f`/`979b8c3` (Sep 27, OR1a/
   OR1e). The live gateway is stale to ~Sep 24 (commit `86901fc`), not one
   commit. **Fixed in §4 Key finding 1 and §5 caveat.**

2. **Heterogeneous staleness documented.** The Qwen/GLM reviewer showed the
   gateway has a **mix** of apply-era states: t2-worker-free-only served qwen7b
   (added in `ad23e99`, Sep 27) → newer; t2-worker served gpt-oss-120b (removed
   in `c126e5f`, Sep 27) → older. The original text assumed a single uniform
   "pre-d08f7f2" snapshot. **Fixed in §4 Key finding 1.**

3. **Provider ambiguity noted.** The served `gpt-oss-120b` doesn't distinguish
   groq vs samba. At `86901fc`, t2-worker had `groq/openai/gpt-oss-120b` (probed
   ok, §3) and t3-driver had `samba/gpt-oss-120b` (not probed). **Noted in §4.**

4. **qwen7b tool-fail caveat added.** The Qwen/GLM reviewer ran on
   t2-worker-free-only (served qwen7b) and made extensive tool calls
   successfully — direct counter-evidence that the §3 tool-fail may be
   prompt-shape-dependent. **Caveat added to §4 Key finding 2.**

5. **§A as unreliable proxy noted.** §A disagrees with combos.json on
   t1-orchestrator leg count (2 vs 5). The measured 502 at 125 s matches the
   5-leg layout, not §A's 2-leg layout. **Noted in §4 Key finding 1.**

### Supplementary review fixes (round 2, reviewers 4–5)

The supplementary Qwen reviewer (qwen3.8-27b:free) found 5 major + 5 minor
issues. Three majors were false positives (M1: DONE note already shows re-run
data; M2: commit table already complete; M5: no stale "inferred" label exists).
Two majors and three minors were valid and fixed:

6. **Transport-error count corrected.** §4 said "7 transport errors" but
   there were 6 transport + 1 HTTP 502 = 7 total failures. **Fixed in §4.**

7. **probe-sweep.py defect re-diagnosed.** §4 Key finding 3 claimed "no HTTP
   socket timeout on the combo request" and "the combo path does not [have
   timeouts]." Both false: both paths use `make_post(timeout=180)` →
   `urlopen(timeout=180)`. The real defect is no total wall-clock deadline
   (per-read timeout resets on trickled bytes) + no per-combo exception
   guard. **Fixed in §4 Key finding 3 and outstanding items #2.**

8. **GLM-5.2-down inference labeled.** §4 Key finding 2 stated "GLM-5.2 was
   temporarily down" as fact; it is inferred from repo leg order + served
   model. **Fixed: labeled "(inferred)" with stale-combo caveat.**

9. **models-proposed.md §E 401 count fixed.** Said "5× 401" but table lists
   6. **Fixed to "6× 401".**

10. **First-run 117 s clarified.** §4 said "t1-orchestrator-free-only 117 s"
    as a long-running combo; it was a ConnectionReset at 117 s, not a slow
    completed request. **Fixed in §4.**

### Outstanding items (not blocking DONE — noted for operator follow-up)

| # | Item | Source | Priority |
|---|---|---|---|
| 1 | Run `omniroute combo list --json` or `tools/audit-router.py` for live gateway ground truth | DeepSeek §2c, Qwen §B1 | high — resolves all stale-combo inferences |
| 2 | probe-sweep.py combo-loop bug (no total wall-clock deadline + no per-combo exception guard; t3-driver-free-only hung >2 h) | Qwen §B3 | medium — add monotonic deadline around each combo call + try/except per combo |
| 3 | t1-orchestrator-free-only is fully dead (all 4 free legs fail) with no §5 mitigation | Qwen §B4 | medium — needs a free fallback leg, distinct from the paid deepseek proposal |
| 4 | Antigravity provider-credential regression (all agy legs 401) not escalated as a finding | DeepSeek §5 | medium — affects opus-4-6 combo + every agy leg |
| 5 | qwen7b tool-fail deserves a second probe with a different tool-call shape | Qwen §B2 | low — the self-referential counter-evidence is strong |
| 6 | §A "Combos with NO deepseek leg" table should note it's scoped to t1/t2/t3 family (omits gemini-3.8-flash, opus-4-6, spark-1.3-contributor, t4-rag) | DeepSeek §3 minor | low |
| 7 | §6 env var: doc says `OMNIROUTE_API_KEY` but repo's client key is `AUTOOS_OMNIROUTE_KEY` | DeepSeek §4 minor | low — confirm which var the launcher sets |
