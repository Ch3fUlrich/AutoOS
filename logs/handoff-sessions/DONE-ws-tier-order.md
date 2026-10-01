# DONE - L1-backlog/ws-tier-order-20261001 (tier order)

**Lane:** `L1-backlog/ws-tier-order-20261001` (TORDER, attempt 2)
**Worktree:** `AutoOS-worktrees/AutoOS-ws-tier-order`
**Base:** `e3436a4d`
**Date:** 2026-10-01
**Evidence:** `docs/handoff/2026-10-01-laneTierOrder.md`
**Scope:** registry routes/models/providers, combos.json fixpoint, all rendered
surfaces, combo-contract gate, tests, skill. No live apply (`-DryRun` only).
Never touched main.

> **REVIEW NONCE: `TORDER-NONCE-C493B548`** — a reviewer must quote this string
> back, read from the evidence file, to prove it read the evidence.

### CHANGELOG bullet

- `feat(routing): tier order TORDER 2026-10-01 — t1 1M-only (>=600k gate, gemini head + free_ai second free + vertex credit + meta/deepseek-last), t2/t3 trial->free->credits->paid deepseek-LAST 128k, gemini 1M/output 65536 + provider re-open (429 backoff), 4 credit singles (ovh x3 + vertex), nebius removed (6), openrouter :free-only (7 paid dropped, t1-clean kept gated), combos fixpoint 26 + all surfaces re-rendered + combo-contract fail-closed (apply+CI+harness+pytest) + skill combo-create`

## Commits

- `018438ed` TASK1 t1 1M (>=600k)
- `ba73f1cf` TASK2 t2/t3 order, vertex, nebius removed
- `20c4a816` TASK3 gemini 1M/65536/usable 1048576, provider re-open
- `f0285616` TASK4 4 credit singles
- `c4c3654b` TORDER-OR openrouter :free-only
- `4f478ba2` TASK5 combos fixpoint (26)
- `b9229fff` TASK6 all surfaces (opencode 1048576/65536 proven)
- `2e67732c` TASK7 contract + wiring + skill
- `ff7e6b09` test-adapt (16 tests)
- this DONE + evidence (pending commit)

## Verify (all exit 0; see evidence for exact outputs)

`registry.py check/validate`, `render omniroute/ide/litellm/openhands/models-doc --check`,
`sync-ide-models.py --check`, `audit-router.py --offline`, `combo-contract.py`
(26 PASS, LIVE 4168), `test_registry.py` 301 passed, `run-tests.ps1 -Filter
"combos.json"` 245/0, `-Filter "ai routing"` 5/0, `apply.ps1 -DryRun` 0.

D-TORDER-2: t1 pair 0 usable (proven) — servable 1M kept, probes needed.
Window audit + live NOT_FOUND (huggingface/antigravity/deepinfra/nebius) in evidence.

## Reviews

- Reviewer 1: family `openrouter/nvidia/nemotron-3-super-120b-a12b:free` (nvidia, free),
  session `ses_f095dfa31ffeh08k3fXfgDOkOK`, verdict **APPROVE** (nonce echoed `TORDER-NONCE-C493B548`).
- Reviewer 2: family `opencode/longcat-2.5-preview-free` (meituan, free; response abbreviated `FREE`),
  session `ses_f095dfa30ffe5FiV98f1bC4Stj`, verdict **APPROVE** (nonce echoed `TORDER-NONCE-C493B548`).
- Rate-limit log: none (`chat_admission_busy`/`Rate limit exceeded` not hit; no backoff needed).
