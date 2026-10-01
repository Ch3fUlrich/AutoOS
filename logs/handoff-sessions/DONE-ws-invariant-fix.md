# DONE — L1-backlog/ws-invariant-fix-20260930 (t1-orchestrator ≥2 usable providers)

**Lane:** `L1-backlog/ws-invariant-fix-20260930`
**Worktree:** `AutoOS-worktrees/AutoOS-ws-invariant`
**Base:** nebius-combos-2 tip `92a98af4`
**Date:** 2026-10-01 (gateway probes 05:58:57–06:04:03Z)
**Writer:** invariant-fix (pinned)
**Evidence:** `docs/handoff/2026-10-01-laneInvariantFix.md`

> **REVIEW NONCE: `INVARFIX-NONCE-2Vb9Xq4M`** — a reviewer must quote this string
> back, read from the evidence file, to prove it read the evidence.

---

## 1. What changed

`groq/qwen/qwen3.8-27b` (a FREEKEYS-1 `tool_calls: proven`, provider-tier-free
free grant) added to `t1-orchestrator`'s free band — after the scaleway legs,
before the paid meta-api/deepseek legs. Before: the route falls to **1** distinct
usable provider (`scaleway`) once L1-beta drops nebius. After: **2** (`groq`,
`scaleway`), so `test_every_agentic_route_has_two_distinct_usable_providers`
stays green.

| File | Change |
|---|---|
| `catalog/ai-registry.json` | `routes.t1-orchestrator.legs` += `groq/qwen/qwen3.8-27b`; dated `T1SECOND` `$comment` |
| `configuration/omniroute/combos.json` | same leg; dated `T1SECOND` `$comment` |
| `configuration/litellm/config.yaml` | re-rendered `t1-orchestrator` block |
| `docs/models.md` | re-rendered `t1-orchestrator` row |

`4 files changed, 24 insertions(+), 3 deletions(-)` for the code/render set.

---

## 2. Verification (green unless noted)

- Invariant test `python -m unittest tests.test_registry.ComboCrossProviderTests.test_every_agentic_route_has_two_distinct_usable_providers` → **ok**; `..._three_usable_legs` → **ok**.
- Counts after simulated beta nebius removal: `t1-orchestrator` **2** (groq, scaleway); other five `4/5/4/5/4` — all six ≥2.
- `registry.py check` + `validate` → `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers` (exit 0).
- `render litellm/ide/openhands/models-doc --check` → **ok**; `render omniroute --check` → drift on **exactly the six nebius combos** (beta's half; §4 of the evidence).
- `audit-router.py --offline` → `no drift`.
- `apply.ps1 -DryRun` → t1-orchestrator plan includes `groq/qwen/qwen3.8-27b`, no nebius; **live apply** → `t1-orchestrator replaced`; live store `Nebius legs live: []`.
- Probes: `t1-orchestrator` → 200 (tool call); `groq/qwen/qwen3.8-27b` → **200 + `get_weather` tool call, max_tokens 512**. No `chat_admission_busy`/`Rate limit exceeded`; no backoff.
- `tests/test_registry.py` 300/1 failure and `tests/test_registry_render.py` 160/5 failures — **identical sets at base** (`AutoOS-ws-nebius2combos`), i.e. no new failure. `test_autoos_resolver.py` 329 OK, `test_sync_ide_models.py` 36 OK.

---

## 3. Instruction note (L0)

The brief said keep the change in `combos.json` and not touch the registry. The
invariant test reads the registry (`load_registry()` + `model.get("tool_calls")`,
`tests/test_registry.py`), so a `combos.json`-only change cannot move it — and
the brief's own fallback ("promote a leg to `proven`") is a registry edit too. I
read "L1-beta's half" as the **nebius removal** (not done here) and made the
minimum registry route-leg addition needed to actually close the defect, plus
its renders. Full reasoning + the exact read path: evidence §0 and §8.

---

## 4. Explicitly NOT done

- No L1-beta nebius removal (registry keeps its nebius legs).
- **No gateway restart.**
- No other lane's files touched.

---

## 5. Reviews

Two `t3-reviewer` leaves, two families (one free), read-only, nonce-gated on
`INVARFIX-NONCE-2Vb9Xq4M`. Verdicts recorded in the follow-up commit to the
evidence file.

| # | Reviewer route (family) | Verdict | Session |
|---|---|---|---|
| 1 | `omniroute/t3-driver-clean` (DeepSeek, paid) | _pending_ | — |
| 2 | free route | _pending_ | — |
