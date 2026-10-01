# DONE — L1-backlog/ws-combos-pins-20260930 (carry main's `24a98228` combos.json pins)

**Lane:** `L1-backlog/ws-combos-pins-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-combospins`
**Base:** `f2d8d607` (combos-chain tip: freewire → nebius removal → invariant fix)
**Date:** 2026-10-01
**Writer:** combos-pins (pinned)
**Evidence:** `docs/handoff/2026-10-01-laneCombosPins.md`
**Scope:** `configuration/omniroute/combos.json` only; the registry half is L1-beta's.

> **REVIEW NONCE: `COMBOSPIN-NONCE-7Qm3Vt9K`** — read from the evidence file.

---

## 1. What changed

`24a98228`'s `combos.json` pins enumerated, then applied where our lineage lacked
them. Only **P5/P6** were missing:

| Combo | Field | Before | After |
|---|---|---|---|
| `t1-orchestrator` | `context` | `128k` | `1M` |
| `t1-orchestrator-free-only` | `context` | `128k` | `1M` |

Plus a `$comment` edit (CTXFIX sentence) and a new dated `MAINPIN 2026-10-01`
block recording the inventory and the decisions.
Diff: `1 file changed, 25 insertions(+), 4 deletions(-)` (commit `2fdf6a7`).

The other four `24a98228` context pins (`deepseek-v4.1-flash`, `gemini-3.8-flash`,
`opus-4-6`, `t2-orchestrator`) were already `1M` on this lineage. The
`deepseek-v4.1-flash` **leg** pin (`deepseek/deepseek-v4-flash`) was deliberately
**not** applied: `origin/main e58274a8` already carries
`deepseek/deepseek-flash` (the P2 pin was superseded inside main by the `d08f7f2`
merge), so applying it would reverse main. Full decisions: evidence §3.

## 2. Verification (all green unless noted)

- `registry.py check` + `validate` → `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers`.
- `render omniroute --check` → **exactly the six known nebius drift lines**
  (beta's half; identical to base).
- `render litellm/ide/openhands/models-doc --check` → all `ok`.
- `tools/audit-router.py --offline` → `no drift`.
- `apply.ps1 -DryRun` → exit 0, correct plan; **no live apply**.
- Registry test files → `6 failed, 459 passed` **both at base and after** (same
  six pre-existing: the OVH-credits-leg test + the five nebius-drift tests).
  `test_audit_router_registry` + `test_mirror_litellm_env_registry` +
  `test_sync_router_tiers_registry` → `1 failed, 42 passed` both.

## 3. Explicitly NOT done

- No `catalog/ai-registry.json` edit (L1-beta owns the six nebius registry rows).
- **No gateway restart, no live apply.**
- No other lane's files touched.

## 4. Reviews

Two `t3-reviewer` leaves, **free model families only** (operator: NOT
`t3-driver-clean`/DeepSeek), read-only, nonce-gated on `COMBOSPIN-NONCE-7Qm3Vt9K`
— **both APPROVED**:

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | `opencode/longcat-2.5-preview-free` (LongCat / Meituan, free) | APPROVED | `ses_f09ab6eaeffeoTiSLXiopWxZ8X` |
| 2 | `opencode/space-bunny-free` (Space Bunny / stealth, free) | APPROVED | `ses_f09ab6ea7ffeFRPEZEgbA4umMP` |

Both quoted the nonce from the evidence file and reproduced the checks
independently. #2 corrected two evidence-narration details (commit count; a
second non-overlapping rebase file `tests/test_autoos_spawner.py`) — fixed in the
evidence file; neither affects the committed `combos.json`. No
`chat_admission_busy` / `Rate limit exceeded` seen; no backoff needed.
