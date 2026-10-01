# DONE — ws-sweep-review-20260930 (2026-09-30)

**Lane:** L1-backlog/ws-sweep-review-20260930
**Worktree:** AutoOS-ws-sweep-review
**Base:** main `d08f7f2`
**Branch under review:** L1-backlog/ws-sweep-20260930, tip `55a87ac` (9 commits)
**Role:** READ-ONLY review (no edits, no push, no merge, no combos.json changes)
**Date:** 2026-09-30T15:12Z
**Reviewer:** L2 t2-worker (model: t2 smart-reasoning-128k)

---

## 1. Commits

| Hash | Message |
|---|---|
| (pending) | docs(handoff): lane sweep review — cross-family verdict, combo divergence, agy credential state |

Single commit on `L1-backlog/ws-sweep-review-20260930` containing:
- `docs/handoff/2026-09-30-laneSweep-review.md` (tracked)
- `logs/handoff-sessions/DONE-ws-sweep-review.md` (git-ignored, added with `-f`)

---

## 2. Reviewer verdicts — 3 families

| # | Reviewer model | Family | Verdict | Session |
|---|---|---|---|---|
| 1 | `omniroute/vertex-flash` (vertex/gemini-3-flash-preview) | Gemini | APPROVED | ses_f0d1d9b05ffe1W2Z5m3I4CxKQZ |
| 2 | `omniroute/deepseek-v4.1-flash` | DeepSeek | APPROVED-WITH-NOTES | ses_f0d202eb9ffeNwJbJVFjM3q54I |
| 3 | `omniroute/t2-worker-free-only` | Qwen/GLM | APPROVED-WITH-NOTES | ses_f0d202eb6ffeRdFZK5wZRvmEdt |

**Overall: APPROVED-WITH-NOTES.** All 3 confirmed the deepseek-fallback proposal is correct.
No reviewer rejected. The Gemini reviewer was a retry — the first attempt
(`omniroute/gemini-3.8-flash`, ses_f0d202ebcffeAgWQnkYN2EKuKx) failed pre-dispatch
(all targets skipped: gemini leg 429, vertex leg unreachable). Retried with
`omniroute/vertex-flash` (direct Vertex AI Gemini 3 Flash, same family) — succeeded.

The Gemini reviewer cited the exact file path it read:
`docs/handoff/2026-09-30-laneSweep-t2-models.md` (the correct evidence doc, not the
gateway-findings file the sweep's own invalid Gemini reviewer read).

---

## 3. Admission / rate-limit log

| # | UTC | Event | Detail |
|---|---|---|---|
| 1 | 15:09Z | Pre-dispatch skip | `omniroute/gemini-3.8-flash` — "all targets were skipped by pre-dispatch filters" (both legs down). Retried with `omniroute/vertex-flash` — succeeded. |

**Total `chat_admission_busy` / `Rate limit exceeded` backoffs: 0.**
No 429/503 admission events in the antigravity probe (3×401, not rate-limit) or
combo probe (1× timeout, 1×401, 1×502, not rate-limit). The Gemini pre-dispatch
skip is an opencode-level availability check, not a gateway admission event.
No 60–120s backoffs were needed.

---

## 4. Key findings

### 4.1 Combo-timeout bug claim is FALSE (high severity)

The sweep's evidence doc §4 "Key finding 3" (lines 193-201) claims the combo
request has "no HTTP socket timeout" and that "the combo path does not" have
timeouts. The DeepSeek reviewer proved this is factually wrong:

- `run_combo_test` calls `post_with_retry(post, …)` where `post` is the closure
  built at `probe-sweep.py:379` (`post = _toolcalls_make_post(args.gateway, key)`).
- That is `probe-toolcalls.py:298` `make_post(..., timeout=180)` →
  `probe_common.py:223` `urllib.request.urlopen(req, timeout=timeout)`.
- The combo request **does** have a 180s socket timeout, byte-for-byte identical
  to the leg path.

The >2h (7200s) hang is **unexplained by the asserted mechanism** — a 180s
socket timeout cannot produce a 7200s hang. The DONE note's action item #4
("Fix probe-sweep.py combo-timeout bug — add 60s socket timeout") should be
**withdrawn** — the code already has the timeout the item asks for. The real
cause of the >2h hang is unproven (possibly gateway-side, not client-side).

### 4.2 Arithmetic error in models-proposed.md §E (medium)

§E update says "10/15 failed (5× 401, …)" — the 401 count is **6, not 5**.
The six 401 legs: antigravity/gemini-3.7-flash-high, antigravity/gemini-3.7-flash-medium,
scw/qwen3, scw/mistral-small, morph/morph-dsv4flash, morph/morph-glm52-744b.
The evidence doc §3 (line 74) and DONE note (line 51) both correctly say 6×401;
only the §E update has the error.

### 4.3 Stale tip hash in DONE note (low)

DONE note line 6 says `Tip: adb515f` but the actual branch tip is `55a87ac`.
`adb515f` does not exist in the repo (unreachable sibling commit). Found by both
DeepSeek and Qwen/GLM reviewers.

### 4.4 Staleness inference refuted by live ground truth (medium, informational)

The sweep's §4 "stale ~6 days" and "heterogeneous staleness" inferences were
about a **pre-15:03Z gateway state** that no longer exists. The gateway was
re-applied at 2026-09-30T15:03Z today (all 15 combos share `createdAt:
15:03:31–15:04:30Z`). The sweep ran at 14:53–14:58Z (before the re-apply).
My ground truth (15:09Z) is post-re-apply.

The sweep correctly noted this needed `omniroute combo list --json` verification
(outstanding item #1). That verification is now done — the staleness inference
is refuted for the current state. The sweep's *symptom* observation (gpt-oss-120b
served but not a repo leg) was valid for the pre-15:03Z state and is still valid
now, but the *root cause* changed: gpt-oss-120b now comes from a new `ovh` provider
(concurrent OVH lane's live apply), not a stale removed leg.

---

## 5. Live-vs-repo combo divergence (omniroute combo list --json, 15:09Z)

**12/15 combos MATCH** (repo legs = live legs, same order).
**3 combos have extra live legs** (total +7 extra).
**0 missing**, **0 stale**.

| # | Combo | Divergence |
|---|---|---|
| 2 | `gemini-3.8-flash` | +1: vertex/gemini-3.8-flash (not in repo) |
| 9 | `t2-worker` | +3: ovh/gpt-oss-120b, ovh/Qwen3-Coder-30B-A3B-Instruct, ovh/Qwen3.8-27B (inserted at positions 6-8) |
| 12 | `t3-driver` | +3: ovh/gpt-oss-120b, ovh/Qwen3-Coder-30B-A3B-Instruct, ovh/Qwen3.8-27B (inserted at positions 4-6) |

All 7 extra legs are from providers not in repo at `d08f7f2`: `vertex` (1) and
`ovh` (6). The 6 `ovh/*` legs are from the OVH lane (`L1-backlog/ws-ovh-20260930`)
which applied its combos live at 15:03Z today. These need reconciliation into
`combos.json` before the next apply.

Full per-combo table is in the review doc §4.

---

## 6. Antigravity credential state (15:10Z)

All 3 antigravity legs return **401 "authentication expired"**:

| Leg | Status | ms |
|---|---|---|
| `antigravity/gemini-3.7-flash-high` | 401 | 29 |
| `antigravity/gemini-3.7-flash-medium` | 401 | 31 |
| `antigravity/claude-opus-4-6-thinking` | 401 | 9 |

Verbatim error: `[antigravity] All 1 connection(s) authentication expired — please reconnect in…`

Sanity legs confirmed probe validity: nebius/GLM-5.2 200/342ms, deepseek 200/2550ms.

**L0 discrepancy UNRESOLVED:** L0 reported re-credentialing antigravity, but the
live gateway still shows 401 "authentication expired" with "All 1 connection(s)"
expired at 15:10Z. The re-credentialing either did not complete, was not yet
effective, or expired again. The measured fact is 401 at 15:10Z, matching the
sweep's 14:33Z measurement — NOT L0's re-credentialing report.

---

## 7. Deepseek-fallback proposal — CORRECT (confirmed by all 3 reviewers)

Appends `deepseek/deepseek-flash` as final fallback leg to 3 combos:
- `t1-orchestrator` (currently: all 5 legs fail, times out >60s)
- `t1-orchestrator-paid` (currently: muse-spark 502)
- `t2-orchestrator` (currently: agy/opus 401)

Does NOT touch the 3 `*-free-only` combos (zero-spend contract verified).
Does NOT touch the 5 combos where deepseek is already a leg or sole leg.

---

## 8. Recommended follow-ups (full list in review doc §7)

1. **Apply deepseek-fallback** to combos.json — OVH/L1-alpha, high
2. **Withdraw DONE note action item #4** (combo-timeout bug is false) — P1-sweep, high
3. **Fix §E arithmetic** "5×401" → "6×401" — P1-sweep, medium
4. **Fix stale tip hash** adb515f → 55a87ac — P1-sweep, low
5. **Add unit test** for `candidate_legs()` — P1-sweep, medium
6. **Reconcile 7 extra live legs** into combos.json (vertex, ovh/*) — OVH/L1-alpha, high
7. **Re-credential antigravity** (still 401) — operator, high
8. **Add logical combo-test timeout** (60s, separate from the 180s socket timeout) — P1-sweep, medium
9. **t1-orchestrator-free-only fully dead** (4 free legs all fail, no deepseek by design) — L1-alpha, medium
10. **Re-run combo routing tests** post-re-apply — P1-sweep, low

---

## 9. Files produced

| File | Tracked | Lines |
|---|---|---|
| `docs/handoff/2026-09-30-laneSweep-review.md` | yes | 317 |
| `logs/handoff-sessions/DONE-ws-sweep-review.md` | no (git-ignored, `-f`) | this file |

## 10. Files reviewed (read-only, no edits)

| File | Source | Lines |
|---|---|---|
| `docs/handoff/2026-09-30-laneSweep-t2-models.md` | sweep (55a87ac) | 398 |
| `logs/handoff-sessions/DONE-ws-sweep.md` | sweep (55a87ac) | 218 |
| `tools/probe-sweep.py` | sweep (55a87ac) | 468 |
| `docs/models-proposed.md` | sweep (55a87ac) | 280 |
| `configuration/omniroute/combos.json` | main (d08f7f2) | 212 |
| Live gateway | `omniroute combo list --json` | 15 combos |

---

## 11. Constraints honored

- **Read-only role:** no edits to any sweep-lane file, no edits to combos.json,
  no push, no merge, no rebase, no checkout.
- **Different families:** Gemini (vertex-flash), DeepSeek, Qwen/GLM — 3 distinct families.
- **Gemini reviewer cited exact file:** `docs/handoff/2026-09-30-laneSweep-t2-models.md`
  (the correct evidence doc, not the gateway-findings file).
- **Probe recipe:** direct-gateway Python with plain model names; never
  `omniroute/` prefix outside opencode; `omniroute combo list --json` for ground truth.
- **Token discipline:** review doc + DONE note only; throwaway probe scripts
  in temp, not committed.
