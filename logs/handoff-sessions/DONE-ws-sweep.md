# DONE — L1-backlog/ws-sweep-20260930 (t2 model gateway sweep)

**Lane:** L1-backlog/ws-sweep-20260930  
**Branch:** `L1-backlog/ws-sweep-20260930` (worktree: `AutoOS-ws-sweep`)  
**Base:** `d08f7f2` (main, 2026-09-30)  
**Tip:** `5f0402c` (10 commits including this DONE note)  
**Date:** 2026-09-30T17:10Z  
**Writer:** t2-worker (this session)  

---

## 1. Commits (9)

| # | Hash | Message |
|---|---|---|
| 1 | `34f5658` | feat(probe): add probe-sweep.py for t2 model sweep (P1-sweep) |
| 2 | `ead67e2` | fix(probe): indent full leg sweep under --combos-only flag |
| 3 | `2828ec7` | docs(handoff): t2 model sweep evidence — leg results, deepseek fallback analysis, qwen CLI verification |
| 4 | `ffddfc4` | docs(models): add 2026-09-30 t2 sweep leg results to models-proposed.md §E |
| 5 | `a8bb198` | docs(handoff): complete combo routing results + gateway instability analysis in evidence doc |
| 6 | `3b160d9` | docs(handoff): apply DeepSeek reviewer fixes — inferred vs measured, remove key length, fix t2-orchestrator URLError |
| 7 | `ec30338` | evidence: replace unstable combo results with clean re-run (6/11 ok) |
| 8 | `73c1192` | evidence: fix stale-combo attribution + add cross-family review (§9) |
| 9 | `bc42e42` | docs(handoff): supplementary cross-family review fixes — transport-error count, probe-sweep.py timeout diagnosis, GLM-5.2 inference label, 401 count, 5 reviewers |

All commits are on the branch only — **not pushed, not merged to main**.

---

## 2. Leg × result matrix (measured 2026-09-30T14:33–14:40Z)

15 candidate legs probed through the live OmniRoute gateway (127.0.0.1:20128).

| # | Leg | Ack | Tool | Ack ms | Ctx | Error |
|---|---|---|---|---|---|---|
| 1 | `gemini/gemini-3.8-flash` | FAIL | FAIL | 96 250 | 1 048 576 | HTTP 429 (rate limit) |
| 2 | `antigravity/gemini-3.7-flash-high` | FAIL | FAIL | 15 | — | HTTP 401 |
| 3 | `scw/qwen3-235b-a22b-instruct-2507` | FAIL | FAIL | 0 | 128 000 | HTTP 401 |
| 4 | `scw/mistral-small-3.2-24b-instruct-2506` | FAIL | FAIL | 15 | 128 000 | HTTP 401 |
| 5 | `nebius/zai-org/GLM-5.2` | **ok** | **ok** | 297 | 128 000 | — |
| 6 | `deepseek/deepseek-flash` | **ok** | **ok** | 1 047 | 1 048 576 | — |
| 7 | `meta-api/muse-spark-1.3-contributor` | FAIL | FAIL | 5 827 | 1 048 576 | HTTP 502 |
| 8 | `free-ai/qwen7b` | **ok** | FAIL | 782 | 128 000 | tool: "expected exactly one tool_call, got 0" |
| 9 | `antigravity/gemini-3.7-flash-medium` | FAIL | FAIL | 15 | — | HTTP 401 |
| 10 | `nebius/zai-org/GLM-5.3-Flash` | FAIL | FAIL | 125 047 | 128 000 | HTTP 504 (125 s timeout) |
| 11 | `mistral/mistral-code-latest` | **ok** | **ok** | 2 297 | 128 000 | — |
| 12 | `groq/openai/gpt-oss-120b` | **ok** | **ok** | 609 | 131 072 | — |
| 13 | `cerebras/gpt-oss-120b` | FAIL | FAIL | 219 | 400 000 | HTTP 402 (payment required) |
| 14 | `morph/morph-dsv4flash` | FAIL | FAIL | 15 | — | HTTP 401 |
| 15 | `morph/morph-glm52-744b` | FAIL | FAIL | 15 | — | HTTP 401 |

**Summary:** 4/15 fully working (GLM-5.2, deepseek-flash, mistral-code-latest, groq/gpt-oss-120b), 1/15 ack-only (qwen7b, no tool support), 10/15 failed (6×401, 1×402, 1×429, 1×502, 1×504).

---

## 3. Combo routing results (clean re-run, measured 2026-09-30T14:53–14:58Z)

A first run at 14:46–14:51Z was discarded (gateway instability: 3/10 ok, 7 transport errors). The clean re-run confirmed those were gateway-overwhelm artifacts.

| # | Combo | Result | Latency ms | Model served | Error |
|---|---|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | **ok** | 1 123 | `deepseek-flash` | — |
| 2 | `t1-orchestrator` | FAIL | 124 728 | (none) | HTTP 502 — all 5 legs failed |
| 3 | `t1-orchestrator-free-only` | FAIL | 122 510 | (none) | HTTP 502 — all 4 legs failed |
| 4 | `t1-orchestrator-paid` | FAIL | 1 497 | (none) | HTTP 502 — sole leg (muse-spark) failed |
| 5 | `t2-orchestrator` | FAIL | 22 | (none) | HTTP 401 — sole leg (agy/opus) no credentials |
| 6 | `t2-worker` | **ok** | 1 251 | `gpt-oss-120b` ⚠ | — (stale combo, see §4 finding 1) |
| 7 | `t2-worker-clean` | **ok** | 864 | `deepseek-flash` | — |
| 8 | `t2-worker-free-only` | **ok** | 1 091 | `qwen7b` | — (GLM-5.2 down → fell through) |
| 9 | `t3-driver` | **ok** | 1 382 | `gpt-oss-120b` ⚠ | — (stale combo, see §4 finding 1) |
| 10 | `t3-driver-clean` | **ok** | 1 161 | `deepseek-flash` | — |
| 11 | `t3-driver-free-only` | **HUNG** | >7 200 000 | (none) | GLM-5.3-Flash 504 leg hung (no socket timeout); killed by PID after >2 h |

**6/11 ok**, 4 FAIL (1× 502 paid, 2× 502 orchestrator, 1× 401), 1 HUNG.

**deepseek-v4.1-flash consistency:** 4/4 ok across all runs (922, 812, 1079, 1123 ms).

**Stale-combo finding:** t2-worker and t3-driver served `gpt-oss-120b` (not a
repo leg). The live gateway is stale to ~2026-09-24 (gpt-oss-120b removed in
`c126e5f`/`979b8c3` on Sep 27). The gateway has **heterogeneous** staleness —
some combos match the repo, some are ~6 days older. Verify with
`omniroute combo list --json` or `tools/audit-router.py`.

---

## 4. Deepseek-v4.1-flash fallback proposal — exact JSON for L1-alpha

**P1-sweep does NOT edit `combos.json`.** This proposal is for the OVH lane
(L1-alpha owns `combos.json`). Apply via `apply.ps1`/`apply.sh` after updating
`combos.json`.

### Combos to modify (3)

#### t1-orchestrator (combos.json line 99-105)
Append `deepseek/deepseek-flash` as final fallback leg:

```json
"models": [
    "gemini/gemini-3.8-flash",
    "scw/qwen3-235b-a22b-instruct-2507",
    "nebius/zai-org/GLM-5.3-Flash",
    "scw/mistral-small-3.2-24b-instruct-2506",
    "meta-api/muse-spark-1.3-contributor",
    "deepseek/deepseek-flash"
]
```

#### t1-orchestrator-paid (combos.json line 119-124)
Append `deepseek/deepseek-flash` as fallback:

```json
"models": [
    "meta-api/muse-spark-1.3-contributor",
    "deepseek/deepseek-flash"
]
```

#### t2-orchestrator (combos.json line 127-132)
Append `deepseek/deepseek-flash` as fallback:

```json
"models": [
    "antigravity/claude-opus-4-6-thinking",
    "deepseek/deepseek-flash"
]
```

### Combos NOT modified (8)

| Combo | Reason |
|---|---|
| `deepseek-v4.1-flash` | deepseek is sole leg (working) |
| `t1-orchestrator-free-only` | free-only by design; adding paid deepseek changes character |
| `t2-worker` | deepseek already leg 6/8 |
| `t2-worker-clean` | deepseek is sole leg (working) |
| `t2-worker-free-only` | free-only by design |
| `t3-driver` | deepseek already leg 5/6 |
| `t3-driver-clean` | deepseek is sole leg (working) |
| `t3-driver-free-only` | free-only by design |

### Rationale

- `deepseek/deepseek-flash` is the most reliable leg in the sweep (4/4 ok,
  ack 1047 ms, tool-call 2843 ms, 1M context).
- The 3 modified combos currently fail entirely when all legs are down
  (measured: t1-orchestrator 502, t1-orchestrator-paid 502, t2-orchestrator 401).
- Appending deepseek as final fallback means the combo degrades to a working
  paid model instead of returning an error.
- Free-only combos are NOT modified: adding a paid leg would change their
  "zero spend" contract.
- The `-clean` twins already pin deepseek as their sole leg.

---

## 5. Qwen CLI path verification (measured 2026-09-30T14:50Z)

**Command:** `omniroute run qwen --model deepseek-v4.1-flash -- --prompt "Reply with exactly: QWEN_CLI_OK"`

**Result:** `QWEN_CLI_OK` (exit 0)

**Status:** Working. The `omniroute run qwen` wrapper injects env + temporary
`QWEN_HOME` config overlay pointing `OPENAI_BASE_URL` at `http://localhost:20128/v1`.
Standalone qwen CLI returns 401 (connects to Alibaba Cloud default, not gateway).

---

## 6. Admission backoff count

| # | UTC timestamp | Request | HTTP | Backoff s | Retried | Outcome |
|---|---|---|---|---|---|---|
| 1 | 14:33:56Z | gemini/gemini-3.8-flash (ack) | 429 | 79 | yes | still 429 (failed) |

**Total:** 1 backoff (leg sweep, 14:33Z). Combo re-run (14:53–14:58Z): 0 backoffs.

---

## 7. Cross-family review verdicts

Five t3-reviewer subagents reviewed the evidence doc across two rounds (3 original
+ 2 supplementary), each on a different model family.

| # | Reviewer model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/deepseek-v4.1-flash` | DeepSeek | APPROVED-WITH-NOTES | ses_f0d2ae02affeHgMyKYPZkhPEZN |
| 2 | `omniroute/gemini-2.5-flash` | Gemini | REJECTED (invalid — reviewed wrong file) | ses_f0d2ae028ffe3mZGupoOz0T0wq |
| 3 | `omniroute/t2-worker-free-only` | Qwen/GLM | APPROVED-WITH-NOTES | ses_f0d2ae025ffeWqTg2tICWUTPBr |
| 4 | `omniroute/vertex-flash` | Gemini (Vertex AI) | APPROVED | ses_f0d2e9f88ffeEeOjv2nt9Xgb3K |
| 5 | `openrouter/qwen/qwen3.8-27b:free` | Qwen (OpenRouter) | NEEDS-CHANGES → fixes applied | ses_f0d2b893dffeluwL9LOTR4hrNZ |

**Valid verdicts:** 4 APPROVED / APPROVED-WITH-NOTES (1, 3, 4, 5→fixed), 1 invalid
(2 — reviewed wrong file). The supplementary Qwen reviewer (#5) found valid issues:
transport-error miscount, probe-sweep.py timeout misdiagnosis, unlabeled inference,
models-proposed.md 401 off-by-one. All fixed. See evidence doc §9 for full detail.

Both original valid reviewers independently identified the stale-combo attribution
as wrong (gpt-oss-120b removed in c126e5f/979b8c3, not d08f7f2). Fixes applied in
commit `73c1192`. Supplementary fixes applied in the final commit.

Gemini reviewer #2's rejection is invalid: it read `omniroute-gateway-findings.md`
instead of `laneSweep-t2-models.md` — a model-follows-wrong-path artifact. The
supplementary Gemini reviewer (#4, vertex-flash) provided a valid APPROVED.

---

## 8. What remains (for operator / L1-alpha follow-up)

| # | Item | Owner | Priority |
|---|---|---|---|
| 1 | Apply the deepseek fallback proposal to `combos.json` (§4 above) | L1-alpha (OVH lane) | high |
| 2 | Run `omniroute combo list --json` or `tools/audit-router.py` for live gateway ground truth | operator | high — resolves all stale-combo inferences |
| 3 | Re-apply current `combos.json` to the live gateway (stale by ~6 days) | operator | high |
| 4 | Fix probe-sweep.py combo-loop bug (add total wall-clock deadline + per-combo try/except; combo path already has 180 s per-read timeout) | P1-sweep or operator | medium |
| 5 | t1-orchestrator-free-only is fully dead (all 4 free legs fail) — needs a free fallback leg | L1-alpha | medium |
| 6 | Antigravity provider-credential regression (all agy legs 401) — affects opus-4-6 + every agy leg | operator | medium |
| 7 | qwen7b tool-fail deserves a second probe with a different tool-call shape | P1-sweep | low |

---

## 9. Files changed

| File | Change |
|---|---|
| `tools/probe-sweep.py` | NEW — sweep script (15 legs + 11 combos) |
| `docs/handoff/2026-09-30-laneSweep-t2-models.md` | NEW — evidence doc (9 sections) |
| `docs/models-proposed.md` | §E updated with 2026-09-30 sweep leg results |
| `logs/handoff-sessions/DONE-ws-sweep.md` | NEW — this DONE note (git-ignored, `git add -f`) |
| `logs/probe-sweep-20260930.jsonl` | git-ignored — 38 records (15 legs + 23 combo records) |
| `configuration/omniroute/combos.json` | **NOT edited** — proposal handed to L1-alpha |
