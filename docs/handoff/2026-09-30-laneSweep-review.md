# Lane Sweep Review — ws-sweep-20260930 (2026-09-30)

**Review lane:** L1-backlog/ws-sweep-review-20260930 (worktree: AutoOS-ws-sweep-review)
**Branch under review:** L1-backlog/ws-sweep-20260930, tip `55a87ac` (9 commits)
**Base:** main `d08f7f2`
**Reviewer:** L2 t2-worker (READ-ONLY role, P1-sweep-review)
**Date:** 2026-09-30T15:12Z

---

## 1. Objective

Replace the invalid Gemini reviewer from the sweep's own cross-family review (§9 of
the evidence doc) and complete the review with 2–3 reviewers from DIFFERENT families,
explicitly including a Gemini-family reviewer that cites the exact file/diff it read.
Produce the live-vs-repo combo divergence table from `omniroute combo list --json` ground
truth. Report the antigravity credential state. Review the deepseek-fallback proposal
(§4 of the DONE note) for correctness.

---

## 2. Cross-family review — 3 reviewers, 3 families

The sweep's own §9 review had 3 reviewers but the Gemini reviewer was invalid (it read
`docs/handoff/2026-09-30-omniroute-gateway-findings.md` — a DIFFERENT file about apply.ps1
failure modes — instead of `docs/handoff/2026-09-30-laneSweep-t2-models.md`). This review
runs 3 fresh reviewers from 3 different families.

| # | Reviewer model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/vertex-flash` (vertex/gemini-3-flash-preview) | **Gemini** | APPROVED | ses_f0d1d9b05ffe1W2Z5m3I4CxKQZ |
| 2 | `omniroute/deepseek-v4.1-flash` | **DeepSeek** | APPROVED-WITH-NOTES | ses_f0d202eb9ffeNwJbJVFjM3q54I |
| 3 | `omniroute/t2-worker-free-only` | **Qwen/GLM** | APPROVED-WITH-NOTES | ses_f0d202eb6ffeRdFZK5wZRvmEdt |

**Note:** The first Gemini attempt used `omniroute/gemini-3.8-flash` but failed —
"Service temporarily unavailable: all targets were skipped by pre-dispatch filters"
(session ses_f0d202ebcffeAgWQnkYN2EKuKx). The gemini-3.8-flash combo's legs are both down
(gemini/gemini-3.8-flash 429, vertex/gemini-3.8-flash unreachable). Retried with
`omniroute/vertex-flash` (direct Vertex AI Gemini 3 Flash, same family) — succeeded.

**Overall verdict: APPROVED-WITH-NOTES.** All 3 reviewers confirmed the deepseek-fallback
proposal is correct. The DeepSeek reviewer found one material factual error (the
combo-timeout bug claim is false — see §4 below). No reviewer rejected.

### 2.1 Gemini-family reviewer (verbatim)

> **VERDICT: APPROVED**
> **FILE READ:** `C:\...\docs\handoff\2026-09-30-laneSweep-t2-models.md` (cited exact path)
>
> 1. [laneSweep-t2-models.md:268-278] Deepseek fallback proposal correctly identifies three non-free combos (`t1-orchestrator`, `t1-orchestrator-paid`, `t2-orchestrator`) that currently return 502/401 errors and proposes appending `deepseek/deepseek-flash` as a final leg.
> 2. [combos.json:99-132] Verified that `deepseek/deepseek-flash` is NOT currently a leg in `t1-orchestrator`, `t1-orchestrator-paid`, or `t2-orchestrator` in the repo at `d08f7f2`.
> 3. [laneSweep-t2-models.md:279-283] Proposal correctly excludes `*-free-only` combos from modification, explicitly stating that adding a paid leg would violate the "zero spend" contract.
> 4. [laneSweep-t2-models.md:124-147] Stale-combo finding correctly identifies that the gateway served `gpt-oss-120b` for `t2-worker` and `t3-driver` despite its removal from `combos.json` in commits `c126e5f` and `979b8c3` (~Sep 27).
> 5. [DONE-ws-sweep.md:94-126] Proposed JSON for deepseek fallback in DONE note §4 correctly appends the model as the final element in the `models` arrays for the three targeted combos.

### 2.2 DeepSeek-family reviewer (verbatim, 10 findings + 3 confirmations)

> **VERDICT: APPROVED-WITH-NOTES**
>
> 1. **[probe-sweep.py:232 + :379] CRITICAL: The central timeout claim in evidence doc §4 "Key finding 3" is factually FALSE.** `run_combo_test` calls `post_with_retry(post, …)`, and `post` is the same closure built at `probe-sweep.py:379` (`post = _toolcalls_make_post(args.gateway, key)`). That is `probe-toolcalls.py:298` `make_post(..., timeout=180)` → `probe_common.py:223` `urllib.request.urlopen(req, timeout=timeout)`. The combo request **does** have a 180 s socket timeout, byte-for-byte identical to the leg path. The doc's text at lines 193-201 ("no HTTP socket timeout on the combo request"; "The leg probe path already has timeouts; the combo path does not") is contradicted by the code.
> 2. **[laneSweep-t2-models.md:189-201] The >2 h hang is unexplained by the asserted mechanism.** A 180 s per-read socket timeout cannot produce a 7 200 s hang, and `post_with_retry` only retries 429/503 for 5+15+45 s; the single admission backoff adds only 60–120 s. The doc states a root cause it has not established; it should say the cause is unproven.
> 3. **[DONE-ws-sweep.md:71,202] The false claim is propagated into the DONE note as operator action item #4** ("Fix probe-sweep.py combo-timeout bug — add 60 s socket timeout"). This is a non-bug and will waste operator time; the item should be withdrawn/corrected.
> 4. **[models-proposed.md §E] Arithmetic error: "10/15 failed (5× 401, …)" — the 401 count is 6, not 5.** The six 401 legs are antigravity/gemini-3.7-flash-high, antigravity/gemini-3.7-flash-medium, scw/qwen3, scw/mistral-small, morph/morph-dsv4flash, morph/morph-glm52-744b. The evidence doc §3 (:74) and DONE note :51 both correctly say 6×401.
> 5. **[probe-sweep.py — no test added] The new tool is uncovered by the suite, contrary to repo convention.** Every sibling probe has a test; `candidate_legs` (:81-123) is a pure function and should have a unit test asserting the derived set/order, per AGENTS.md §5.
> 6. **[DONE-ws-sweep.md:6] Stale self-referential tip hash.** The note says `Tip: adb515f`, but the branch tip is `55a87ac`; `adb515f` is an unreachable sibling commit.
> 7. **[probe-sweep.py:64,373] `/v1/models` metadata ignores `--gateway`.** `fetch_models_metadata` always uses `GATEWAY_BASE` derived from `DEFAULT_GATEWAY`, while the leg and combo probes use `args.gateway`.
> 8. **[probe-sweep.py:326-338] Dead code in the `--legs` filter.** The loop `for leg in args.legs: if leg not in leg_set: pass` can never execute its body.
> 9. **[probe-sweep.py:81] `candidate_legs(registry)` never uses its `registry` parameter** — it reads combos.json directly. Dead parameter.
> 10. **[laneSweep-t2-models.md:81,106] Re-run window mislabelled.** §4 is headed "14:53–14:58Z" yet combo #11 is recorded as hanging "until 16:59Z".
>
> **CONFIRMATIONS:** candidate_legs derivation correct (:81-123); idempotency acceptable (append-mode log, git-ignored); no secret/binary/username leak (probe-sweep.py:363-364 never interpolates the key); no AGENTS.md §1 violations.

### 2.3 Qwen/GLM-family reviewer (verbatim, 5 findings)

> **VERDICT: APPROVED-WITH-NOTES**
>
> 1. [models-proposed.md:59 vs combos.json:99-105] §A UNRELIABILITY CONFIRMED. §A shows t1-orchestrator with 2 legs (spark-only); combos.json shows 5 legs (free-first chain). Legs are entirely different, not just different in count.
> 2. [laneSweep-t2-models.md:179-187] QWEN7B TOOL-FAIL CAVEAT CONFIRMED CORRECT. Appropriately hedged ("may be prompt-shape-dependent"). Self-referential (reviewer ran on t2-worker-free-only, same combo) but valid empirical counter-evidence.
> 3. [laneSweep-t2-models.md:147-156] HETEROGENEOUS STALENESS CONFIRMED CORRECTLY REASONED. Combines measured serving data with git-history inference. Already flagged as unverifiable at lines 162-163 (recommends `omniroute combo list --json`). Appropriately hedged.
> 4. [DONE-ws-sweep.md:127-138 + combos.json:108-117] DEEPSEEK-FREEONLY INTERACTION CONFIRMED CORRECT. t1-orchestrator-free-only listed in "NOT modified" table. No free-only combo receives a paid leg.
> 5. [DONE-ws-sweep.md:6] MINOR: DONE note states tip `adb515f` but actual tip is `55a87ac`. Stale/fabricated hash.

---

## 3. Deepseek-fallback proposal review (§4 of DONE note, §5 of evidence doc)

**Verdict: CORRECT** — confirmed by all 3 reviewers independently.

The proposal appends `deepseek/deepseek-flash` as the final fallback leg to 3 combos:

| Combo | Current legs (tail) | Proposed tail | Correct? |
|---|---|---|---|
| `t1-orchestrator` | …→ muse-spark (502) | …→ muse-spark → **deepseek/deepseek-flash** | ✓ |
| `t1-orchestrator-paid` | muse-spark (502) | muse-spark → **deepseek/deepseek-flash** | ✓ |
| `t2-orchestrator` | agy/opus-4-6-thinking (401) | agy/opus-4-6-thinking → **deepseek/deepseek-flash** | ✓ |

**Not modified (8 combos):**

| Combo | Reason | Correct? |
|---|---|---|
| `deepseek-v4.1-flash` | deepseek is sole leg (working) | ✓ |
| `t1-orchestrator-free-only` | free-only by design (zero-spend contract) | ✓ |
| `t2-worker` | deepseek already leg 6/8 | ✓ |
| `t2-worker-clean` | deepseek is sole leg (working) | ✓ |
| `t2-worker-free-only` | free-only by design | ✓ |
| `t3-driver` | deepseek already leg 5/6 | ✓ |
| `t3-driver-clean` | deepseek is sole leg (working) | ✓ |
| `t3-driver-free-only` | free-only by design | ✓ |

**Zero-spend contract verified:** No `*-free-only` combo receives a paid leg. The 3
modified combos are NOT free-only — they are the paid/mixed orchestrator combos that
currently fail entirely (502/401). Adding deepseek as a final fallback means they
degrade to a working paid model instead of returning an error.

**Live gateway confirmation (my combo probe, 15:10Z):** All 3 target combos still fail:
- `t1-orchestrator`: timed out at 60s (all 5 legs fail)
- `t1-orchestrator-paid`: 502 (muse-spark 502, 2360ms)
- `t2-orchestrator`: 401 (agy/opus 401, 42ms)

The proposal is still needed — these combos have no working leg without the deepseek
fallback. `deepseek/deepseek-flash` is the most reliable leg in the sweep (4/4 ok,
<1.1s ack).

---

## 4. Live-vs-repo combo divergence table

**Method:** `omniroute combo list --json` (read-only, 2026-09-30T15:09Z) compared against
`configuration/omniroute/combos.json` at `d08f7f2` (main, this lane's base).

**Key finding:** All 15 live combos share `createdAt`/`updatedAt` timestamps of
**2026-09-30T15:03:31–15:04:30Z** — the gateway was **re-applied today at 15:03Z**
(after the sweep ran at 14:33–14:58Z). This is NOT a 6-day-stale gateway; it was just
re-applied. The sweep's §4 "stale ~6 days" inference was about a **pre-15:03Z gateway
state** that no longer exists.

### Per-combo divergence (15 combos)

| # | Combo | Repo legs (d08f7f2) | Live legs (15:03Z) | Divergence |
|---|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | [deepseek/deepseek-flash] | [deepseek/deepseek-flash] | **MATCH** |
| 2 | `gemini-3.8-flash` | [gemini/gemini-3.8-flash] | [gemini/gemini-3.8-flash, **vertex/gemini-3.8-flash**] | **+1 extra live leg** |
| 3 | `opus-4-6` | [antigravity/claude-opus-4-6-thinking] | [antigravity/claude-opus-4-6-thinking] | **MATCH** |
| 4 | `spark-1.3-contributor` | [meta-api/muse-spark-1.3-contributor] | [meta-api/muse-spark-1.3-contributor] | **MATCH** |
| 5 | `t1-orchestrator` | 5 legs (gemini→scw-qwen3→nebius-GLM-5.3-Flash→scw-mistral→muse-spark) | same 5 legs, same order | **MATCH** |
| 6 | `t1-orchestrator-free-only` | 4 legs (gemini→scw-qwen3→nebius-GLM-5.3-Flash→scw-mistral) | same 4 legs, same order | **MATCH** |
| 7 | `t1-orchestrator-paid` | [meta-api/muse-spark] | [meta-api/muse-spark] | **MATCH** |
| 8 | `t2-orchestrator` | [antigravity/claude-opus-4-6-thinking] | [antigravity/claude-opus-4-6-thinking] | **MATCH** |
| 9 | `t2-worker` | 8 legs: gemini→agy-high→scw-qwen3→scw-mistral→nebius-GLM-5.2→deepseek→muse-spark→qwen7b | 11 legs: gemini→agy-high→scw-qwen3→scw-mistral→nebius-GLM-5.2→**ovh/gpt-oss-120b→ovh/Qwen3-Coder-30B-A3B-Instruct→ovh/Qwen3.8-27B**→deepseek→muse-spark→qwen7b | **+3 extra live legs** (ovh/* inserted at positions 6-8) |
| 10 | `t2-worker-clean` | [deepseek/deepseek-flash] | [deepseek/deepseek-flash] | **MATCH** |
| 11 | `t2-worker-free-only` | 6 legs (gemini→agy-med→scw-qwen3→scw-mistral→nebius-GLM-5.2→qwen7b) | same 6 legs, same order | **MATCH** |
| 12 | `t3-driver` | 6 legs: scw-mistral→nebius-GLM-5.2→scw-qwen3→mistral-code-latest→deepseek→muse-spark | 9 legs: scw-mistral→nebius-GLM-5.2→scw-qwen3→**ovh/gpt-oss-120b→ovh/Qwen3-Coder-30B-A3B-Instruct→ovh/Qwen3.8-27B**→mistral-code-latest→deepseek→muse-spark | **+3 extra live legs** (ovh/* inserted at positions 4-6) |
| 13 | `t3-driver-clean` | [deepseek/deepseek-flash] | [deepseek/deepseek-flash] | **MATCH** |
| 14 | `t3-driver-free-only` | 4 legs (scw-mistral→nebius-GLM-5.3-Flash→scw-qwen3→qwen7b) | same 4 legs, same order | **MATCH** |
| 15 | `t4-rag` | [cohere/command-a-03-2025, cohere/command-r-plus-08-2024] | same 2 legs, same order | **MATCH** |

**Summary:** 12/15 combos MATCH (repo = live). 3 combos have **extra live legs** (total
+7 extra legs). 0 combos have **missing** legs (all repo legs present in live). 0
combos have **stale** legs (no removed repo legs in live). All 7 extra legs are from
providers not in the repo at `d08f7f2`: `vertex/gemini-3.8-flash` (1) and `ovh/*` (6).

### Divergence source

The 6 `ovh/*` extra legs in `t2-worker` and `t3-driver` are from the **OVH lane**
(`L1-backlog/ws-ovh-20260930`, worktree `AutoOS-ws-ovh` at `0b102f6`) — a concurrent lane
that applied its combos live at 15:03Z today. These legs are NOT in the repo at `d08f7f2`
(main, this lane's base) because the OVH lane's changes are on a branch, not yet merged
to main. The `vertex/gemini-3.8-flash` extra leg in the `gemini-3.8-flash` combo is from
the Vertex AI connection (registered from the `vertex_ai` key, per the opencode config).

### Sweep's staleness claims vs live ground truth

| Sweep claim | Assessment | Evidence |
|---|---|---|
| "stale ~6 days" (Sep 24 → Sep 30) | **REFUTED** — gateway re-applied at 15:03Z today | All combos `createdAt: 2026-09-30T15:03-15:04Z` |
| "gpt-oss-120b served but not a repo leg" | **CONFIRMED** — live t2-worker has `ovh/gpt-oss-120b` (leg 6), t3-driver has `ovh/gpt-oss-120b` (leg 4) | `omniroute combo list --json` + my combo probe (15:10Z, both served gpt-oss-120b) |
| "heterogeneous staleness" (mix of apply-era states) | **REFUTED for current state** — all combos share the same `createdAt` (15:03Z) | `omniroute combo list --json` — uniform timestamps |
| "gpt-oss-120b provider is groq/samba" (inferred) | **REFUTED** — live gateway has `ovh/gpt-oss-120b` (OVH provider), not groq/samba | `omniroute combo list --json` — `providerId: "ovh"` |
| "gateway stale to ~Sep 24 (commit 86901fc)" | **MOOT** — the pre-15:03Z state the sweep measured was replaced at 15:03Z | The sweep ran at 14:53Z (pre-re-apply); my ground truth is from 15:09Z (post-re-apply) |

**Important caveat:** The sweep's §4 measurements were taken at **14:53–14:58Z**, BEFORE
the 15:03Z re-apply. The sweep's observations (t2-worker served gpt-oss-120b, t3-driver
served gpt-oss-120b) were VALID for that pre-15:03Z state. But the sweep's INFERENCE
about WHY (staleness to Sep 24, heterogeneous) was unverifiable at the time and is now
superseded — the current gateway has gpt-oss-120b from a NEW provider (ovh), not a stale
removed leg. The sweep correctly identified the symptom (gpt-oss-120b not a repo leg)
but the root cause changed between the sweep's measurement and my ground truth.

### Combo behavior confirmation (my probe, 2026-09-30T15:10Z)

| Combo | Status | ms | Model served | Sweep's result (14:53Z) | Match? |
|---|---|---|---|---|---|
| `t2-worker` | 200 | 1896 | `gpt-oss-120b` | ok, 1251ms, `gpt-oss-120b` | ✓ (still serves gpt-oss-120b) |
| `t3-driver` | 200 | 1467 | `gpt-oss-120b` | ok, 1382ms, `gpt-oss-120b` | ✓ (still serves gpt-oss-120b) |
| `t1-orchestrator` | timeout | 60012 | (none) | FAIL 502, 124728ms | ✓ (still fails, >60s) |
| `t2-orchestrator` | 401 | 42 | (none) | FAIL 401, 22ms | ✓ (still 401) |
| `t1-orchestrator-paid` | 502 | 2360 | (none) | FAIL 502, 1497ms | ✓ (still 502) |
| `deepseek-v4.1-flash` | 200 | 1013 | `deepseek-flash` | ok, 1123ms | ✓ (sanity OK) |

**Note:** `t2-worker` and `t3-driver` still serve `gpt-oss-120b` even though the live
gateway has `nebius/zai-org/GLM-5.2` at an earlier position (leg 5 / leg 2) and GLM-5.2
works in direct leg probes (342ms). The gateway's breaker/cooldown or per-request
routing may be skipping GLM-5.2 in the combo path. This is an observation, not a defect
in the sweep — the sweep correctly reported what it measured.

---

## 5. Antigravity credential state (measured 2026-09-30T15:10Z)

**Probe command:** standalone Python script (direct gateway `POST
http://127.0.0.1:20128/v1/chat/completions`, plain model names, key from
`AUTOOS_OMNIROUTE_KEY` env, never printed).

| Leg | Status | ms | Error (verbatim, truncated) |
|---|---|---|---|
| `antigravity/gemini-3.7-flash-high` | 401 | 29 | `[antigravity] All 1 connection(s) authentication expired — please reconnect in…` |
| `antigravity/gemini-3.7-flash-medium` | 401 | 31 | `[antigravity] All 1 connection(s) authentication expired — please reconnect in…` |
| `antigravity/claude-opus-4-6-thinking` | 401 | 9 | `[antigravity] All 1 connection(s) authentication expired — please reconnect in…` |
| `nebius/zai-org/GLM-5.2` (sanity) | 200 | 342 | — (served `zai-org/GLM-5.2`) |
| `deepseek/deepseek-flash` (sanity) | 200 | 2550 | — (served `deepseek-flash`) |

**Finding:** All 3 antigravity legs return **401 "authentication expired"** at 15:10Z.
This **confirms the sweep's 401 measurement** (14:33Z). The sweep's §3 failure category
table (line 74) correctly lists 6×401 legs including both agy variants.

**L0 re-credentialing discrepancy:** L0 reported re-credentialing antigravity, but the
live gateway still shows 401 "authentication expired" with "All 1 connection(s)"
expired. The re-credentialing either (a) did not complete successfully, (b) was reported
but not yet effective, or (c) expired again between L0's report and my probe. The
discrepancy is NOT resolved in favor of L0 — the measured fact is 401 "authentication
expired" at 15:10Z, matching the sweep's 14:33Z measurement.

**Impact:** `t2-orchestrator` (sole leg: antigravity/claude-opus-4-6-thinking) returns
401 — this combo is completely non-functional until antigravity credentials are restored.
The deepseek-fallback proposal correctly mitigates this by appending
`deepseek/deepseek-flash` as a fallback leg.

---

## 6. Combined findings (reviewer + reviewer-of-record)

### Material findings (affect the evidence doc's accuracy)

| # | Finding | Source | Severity | File:line |
|---|---|---|---|---|
| M1 | **Combo-timeout bug claim is FALSE.** The combo request DOES have a 180s socket timeout (via `make_post` → `urlopen(timeout=180)`). The >2h hang is unexplained by the asserted mechanism. The evidence doc §4 Key finding 3 (lines 193-201) is factually wrong. DONE note action item #4 (line 202) should be withdrawn. | DeepSeek reviewer §2.1-2.3 | **high** | laneSweep-t2-models.md:193-201, DONE-ws-sweep.md:71,202 |
| M2 | **Arithmetic error in models-proposed.md §E.** "5× 401" should be "6× 401". The evidence doc §3 and DONE note §2 both correctly say 6×401; only the §E update has the error. | DeepSeek reviewer §2.4 | **medium** | models-proposed.md §E (added block, ~line 226) |
| M3 | **Stale tip hash in DONE note.** Says `adb515f` but actual tip is `55a87ac`. `adb515f` does not exist in the repo. | DeepSeek + Qwen/GLM reviewers | **low** | DONE-ws-sweep.md:6 |
| M4 | **"stale ~6 days" inference REFUTED.** Gateway was re-applied at 15:03Z today. The sweep's staleness inference was about a pre-15:03Z state that no longer exists. The sweep correctly noted this needed `omniroute combo list --json` verification (outstanding item #1). | This review (live ground truth) | **medium** (informational) | laneSweep-t2-models.md:165-170 |
| M5 | **"heterogeneous staleness" REFUTED for current state.** All combos share `createdAt: 15:03Z`. The inference was about a superseded gateway state. | This review (live ground truth) | **medium** (informational) | laneSweep-t2-models.md:147-156 |

### Non-material findings (code quality / convention)

| # | Finding | Source | File:line |
|---|---|---|---|
| N1 | No test added for probe-sweep.py (every sibling probe has a test; `candidate_legs` is a pure function) | DeepSeek reviewer §2.5 | probe-sweep.py:81-123 |
| N2 | `/v1/models` metadata ignores `--gateway` flag | DeepSeek reviewer §2.7 | probe-sweep.py:64,373 |
| N3 | Dead code in `--legs` filter | DeepSeek reviewer §2.8 | probe-sweep.py:326-338 |
| N4 | `candidate_legs(registry)` never uses `registry` parameter | DeepSeek reviewer §2.9 | probe-sweep.py:81 |
| N5 | Re-run window mislabelled (says 14:53-14:58Z but combo #11 hung until 16:59Z) | DeepSeek reviewer §2.10 | laneSweep-t2-models.md:81,106 |

### Confirmations (verified correct by reviewers)

| # | Item | Source |
|---|---|---|
| C1 | Deepseek-fallback proposal correct (3 combos modified, 8 not modified, free-only untouched) | All 3 reviewers |
| C2 | deepseek NOT already a leg in the 3 target combos | Gemini + DeepSeek reviewers |
| C3 | Zero-spend contract not violated (no paid leg added to free-only combos) | All 3 reviewers |
| C4 | §A unreliability confirmed (t1-orchestrator: 2 legs in §A vs 5 in combos.json) | Qwen/GLM reviewer |
| C5 | qwen7b tool-fail caveat correctly hedged (self-referential but valid) | Qwen/GLM reviewer |
| C6 | No secrets, binaries, or username paths in any file | DeepSeek reviewer |
| C7 | Idempotency: append-mode log, git-ignored, no tracked file written | DeepSeek reviewer |
| C8 | Stale-combo finding correctly identifies gpt-oss-120b as not a repo leg | Gemini reviewer |

---

## 7. Recommended follow-ups

| # | Item | Owner | Priority |
|---|---|---|---|
| 1 | **Apply the deepseek-fallback proposal** to `combos.json` (append `deepseek/deepseek-flash` to t1-orchestrator, t1-orchestrator-paid, t2-orchestrator) | OVH lane (L1-alpha owns combos.json) | high |
| 2 | **Withdraw DONE note action item #4** (combo-timeout bug is false — the code has a 180s timeout). Investigate the real cause of the >2h hang (possibly gateway-side, not client-side). | P1-sweep follow-up | high |
| 3 | **Fix arithmetic error** in models-proposed.md §E: "5× 401" → "6× 401" | P1-sweep follow-up | medium |
| 4 | **Fix stale tip hash** in DONE note: `adb515f` → `55a87ac` | P1-sweep follow-up | low |
| 5 | **Add unit test** for `candidate_legs()` in probe-sweep.py (repo convention) | P1-sweep follow-up | medium |
| 6 | **Re-apply current combos.json to the live gateway** — the 7 extra live legs (vertex, ovh/*) are from concurrent lanes' applies, not in repo at d08f7f2. The OVH lane's ovh/* legs and the vertex leg need to be reconciled into combos.json before the next apply. | OVH lane + L1-alpha | high |
| 7 | **Re-credential antigravity** — all 3 legs still 401 "authentication expired" at 15:10Z. L0's re-credentialing did not take effect. Affects t2-orchestrator (sole leg) and every agy leg in t2-worker/t2-worker-free-only. | operator | high |
| 8 | **Add a combo-path timeout** to the probe-sweep.py combo test SEPARATELY from the existing 180s socket timeout — 180s is too long for a combo ack test (no working combo took >1.4s). A 60s *logical* timeout (separate from the socket timeout) would prevent the >2h hang class. | P1-sweep follow-up | medium |
| 9 | **t1-orchestrator-free-only is fully dead** (all 4 free legs fail) with no deepseek mitigation by design. Needs a free fallback leg. | L1-alpha | medium |
| 10 | **Re-run the combo routing tests** post-re-apply to confirm whether t2-worker/t3-driver now serve GLM-5.2 (leg 5/2) or still serve gpt-oss-120b (ovh, leg 6/4). My probe at 15:10Z still showed gpt-oss-120b, but GLM-5.2 works in direct leg probes. | P1-sweep follow-up | low |

---

## 8. Admission / rate-limit log

| # | UTC timestamp | Event | Detail |
|---|---|---|---|
| 1 | 15:09Z | Gemini reviewer pre-dispatch skip | `omniroute/gemini-3.8-flash` — "all targets were skipped by pre-dispatch filters" (both legs down: gemini 429, vertex unreachable). Retried with `omniroute/vertex-flash` — succeeded. |

**Total `chat_admission_busy` / `Rate limit exceeded` events: 0.** No 429/503 admission
backoffs in the antigravity probe (3×401, not rate-limit) or combo probe (1× timeout,
1×401, 1×502, not rate-limit). The only failure was the pre-dispatch filter skip on the
Gemini combo (an opencode-level availability check, not a gateway admission event).

---

## 9. Files reviewed

| File | Source | Lines |
|---|---|---|
| `docs/handoff/2026-09-30-laneSweep-t2-models.md` | sweep branch (55a87ac) | 398 |
| `logs/handoff-sessions/DONE-ws-sweep.md` | sweep branch (55a87ac) | 218 |
| `tools/probe-sweep.py` | sweep branch (55a87ac) | 468 |
| `docs/models-proposed.md` | sweep branch (55a87ac) | 280 (§E update) |
| `configuration/omniroute/combos.json` | main (d08f7f2) | 212 |
| `docs/handoff/2026-09-30-omniroute-gateway-findings.md` | main (d08f7f2) | 30 (header only — confirmed it is a different file) |
| Live gateway | `omniroute combo list --json` | 15 combos |
