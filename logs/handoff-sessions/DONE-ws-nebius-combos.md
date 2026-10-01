# DONE — L1-backlog/ws-nebius2-combos-20260930 (nebius removal from combos.json)

**Lane:** `L1-backlog/ws-nebius2-combos-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-nebius2combos`
**Base:** FREEWIRE wiring tip `2ff537a` (free legs wired, gemini excluded)
**Date:** 2026-10-01 (gateway probes 05:45:47–05:45:50Z)
**Writer:** nebius-combos-2 (pinned)
**Evidence:** `docs/handoff/2026-10-01-laneNebiusCombos.md`
**Scope:** `configuration/omniroute/combos.json` only; registry half is L1-beta's.

> **REVIEW NONCE: `NEBREMOVAL-NONCE-4Kt7Wq2Z`** — a reviewer must quote this
> string back, read from the evidence file, to prove it read the evidence.

---

## 1. What changed

Six `nebius/*` legs removed from `configuration/omniroute/combos.json` — the
file now carries **zero** nebius model refs:

| Combo | Removed leg |
|---|---|
| `t1-orchestrator` | `nebius/zai-org/GLM-5.3-Flash` |
| `t1-orchestrator-free-only` | `nebius/zai-org/GLM-5.3-Flash` |
| `t2-worker` | `nebius/zai-org/GLM-5.2` |
| `t2-worker-free-only` | `nebius/zai-org/GLM-5.2` |
| `t3-driver` | `nebius/zai-org/GLM-5.2` |
| `t3-driver-free-only` | `nebius/zai-org/GLM-5.3-Flash` |

Plus one dated `NEBREMOVAL` `$comment` block; older history comments intact.
Diff: `1 file changed, 14 insertions(+), 7 deletions(-)`.

---

## 2. Verification (all green unless noted)

- `registry.py check` + `validate` → `ok: registry 2026-09-30, 32 routes, 80
  models, 34 providers` (exit 0). The registry is untouched, so it still passes.
- `render omniroute --check` → drift on **exactly the six edited combos**
  (expected registry-vs-combos drift; beta clears it — evidence §4). All four
  **other** renders + `sync-ide-models.py --check` → `ok`.
- `tools/audit-router.py --offline` → `no drift`.
- `apply.ps1 -DryRun` → correct plan, no nebius in any combo; **live apply** →
  22/22 combos replaced, resilience already current.
- `omniroute combo list --json` → `TOTAL combos: 22 / Nebius legs in live store: 0`.
- Ack probes on all six touched combos → **6/6 HTTP 200** (05:45:47–05:45:50Z),
  no 429/503/504, no `chat_admission_busy`.
- `tests/test_registry.py` → 1 failure, the **pre-existing**
  `test_a_credit_leg_is_last_and_gated_until_priced` (identical at base).

---

## 3. Invariant finding to ACTION (L1-beta / L0)

Under the repo's own `usable_legs` metric, `t1-orchestrator` falls from **2**
distinct usable providers to **1** (both remaining usable legs are scaleway)
once nebius is removed; its other live legs (`meta_api/muse-spark-1.3-contributor`,
`deepseek/deepseek-flash`) are `tool_calls: unproven`. The other five agentic
routes stay ≥2 (4/5/4/5/4). This is **not** introduced by the combos edit — the
live gateway already served no nebius leg — but beta's registry removal will
make `test_every_agentic_route_has_two_distinct_usable_providers` fail unless a
second distinct-provider usable leg is added/marked. Full detail: evidence §7.

---

## 4. Explicitly NOT done

- No `catalog/ai-registry.json` edit (L1-beta owns it).
- **No gateway restart.**
- No other lane's files touched.

---

## 5. Reviews

Recorded after the lane commit; see evidence §10. (Filled in the follow-up
commit.)
