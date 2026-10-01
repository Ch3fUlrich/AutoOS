# DONE — L1-backlog/ws-gemini-restore-20260930 (gemini exclusion cancelled, heads restored)

**Branch:** `L1-backlog/ws-gemini-restore-20260930`
**Worktree:** `C:\Users\mauls\Documents\Code\AutoOS-worktrees\AutoOS-ws-geminirestore` (proved: `git rev-parse --show-toplevel` = that path)
**Base:** `ba0c707f` (combos-pins tip + main `24a98228` pins)
**Date:** 2026-10-01
**Evidence:** `docs/handoff/2026-10-01-laneGeminiRestore.md` (nonce `GEMRESTORE-NONCE-4Xk9Qm2Z`, position table, decisions, quoted verification, registry flags, wording spots, reviews)

## What was done

Operator reversal: the gemini exclusion is CANCELLED. Restored `gemini/gemini-3.8-flash` to its pre-freewire (`a975d48`) head positions in `configuration/omniroute/combos.json` (commit `e3436a4`): `gemini-3.8-flash` combo pos0 head with `vertex/gemini-3.8-flash` second, plus pos0 heads on `t1-orchestrator`, `t1-orchestrator-free-only`, `t2-worker`, `t2-worker-free-only` (all PREPENDED, no free leg displaced). `t3-driver`/`t3-driver-free-only` unchanged (no gemini pre-freewire). Kept: 10 proven free legs + allow-rules, nebius removal (verified `[]`), T1SECOND `groq/qwen/qwen3.8-27b` leg, OVH legs, 1M contexts, main `24a98228` pins. `catalog/ai-registry.json` untouched (L1-beta's file; flags recorded with exact lines). Standing wording now: **gemini retained; usage governed by the repeated-429 backoff policy (3×429/120 s → 300 s cooldown per leg; 30 min park)** (resilience `51fd01d`…`13d853e4`, verified present on `ws-resilience-env`, absent here as expected).

## Verification (quoted in evidence)

- `render omniroute --check`: seven differs lines (six nebius + `gemini-3.8-flash`; base had six nebius; delta is beta's registry gap, recorded).
- `render litellm/ide/openhands/models-doc --check`: all `ok`.
- `registry.py check` + `validate`: `ok: registry 2026-09-30, 32 routes, 80 models, 34 providers`.
- `audit-router.py --offline`: `no drift`.
- `apply.ps1 -DryRun`: `Gateway OK`, gemini heads planned, NO live apply; pre-existing case-sensitivity warning + no-keys notice (public-only lane).
- Tests: `test_registry+render+loader` base `6 failed, 459 passed` → after `6 failed, 459 passed` (same six names; diffs 6→7 combos); `audit+mirror+sync` base `1 failed, 42 passed` → after `1 failed, 42 passed` (same test; drift 6→7 combos). No new failures.
- Ack probe: SKIP — unauthenticated `GET /v1/models` → `HTTP Error 401: Unauthorized`; no `api-keys.yml` in this public-only worktree; zero completion attempts (no hammering).
- Resilience grep: six env names absent here, proved present on `ws-resilience-env@13d853e4` (`apply.ps1:94-110`, `apply.sh:455-460`, `start-stack.ps1:117-133`, `start-stack.sh:86-91`).

## Reviews (nonce-gated, FREE only, never t3-driver-clean)

- `ses_f096ea65fffet1N2oQr0lsxOdv` (FREE pool) — APPROVED
- `ses_f096df7b9ffebSRYYl75P1HhR2` (`omniroute/or-nemotron-3-super-free`, NVIDIA FREE) — APPROVED

## CHANGELOG bullet

- `fix(routing): cancel the gemini exclusion — restore gemini/gemini-3.8-flash to its pre-freewire head positions (gemini-3.8-flash combo head + vertex second; t1-orchestrator, t1-orchestrator-free-only, t2-worker, t2-worker-free-only heads), keeping the 10 proven free legs, nebius removal, T1SECOND groq leg, OVH legs, 1M contexts and main 24a98228 pins; gemini retained under the repeated-429 backoff policy (3×429/120 s → 300 s cooldown; 30 min park).`

## Flags for L0

- L1-beta: mirror gemini heads + re-open `providers.google_ai_studio` under backoff (exact lines in evidence §3).
- Historical `gemini.*exclu` titles/sections listed in evidence §4 for a reversal addendum if wanted.
- Combined apply is L0/operator-canonical (this lane DryRun only).
