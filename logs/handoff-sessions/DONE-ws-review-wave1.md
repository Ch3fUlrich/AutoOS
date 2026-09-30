# DONE — ws-review-20260930 (Wave 1)

**Date:** 2026-09-30
**Lane:** L1 review (t2 smart-reasoning-128k orchestrator)
**Base:** d08f7f2 (main == origin/main at branch cut)
**Worktree:** `C:\Users\<user>\Documents\Code\AutoOS-worktrees\AutoOS-ws-review`
**Branch:** `L1-backlog/ws-review-20260930`

---

## Deliverable

`docs/handoff/2026-09-30-laneReview-wave1.md` — independent cross-family review
of two completed milestones by 2 reviewer leaves from different model families
each, run sequentially.

## Milestones reviewed

| Milestone | Writer lane | Commit | Reviewers | Verdict |
|---|---|---|---|---|
| Combos | L1-alpha | a5bcb69 | DeepSeek + Vertex/Gemini | APPROVE / APPROVE |
| P0 / Admission | L1-beta | d88ed3b | DeepSeek + Vertex/Gemini | REJECT / REJECT |

## Reviewer families

| Family | Model ID |
|---|---|
| DeepSeek | `omniroute/deepseek-v4.1-flash` |
| Vertex / Gemini | `omniroute/vertex-3.8-flash` |

Both families were pre-verified working before reviewer spawns. Reviewers were
run sequentially (one at a time) for rate-limit hygiene.

## Key findings

### Combos (a5bcb69) — APPROVED by both reviewers

- All 6 invariants passed (vertex leg-2 assignment, 5-combo scope, no
  free-only pollution, registry untouched, JSON valid, apply.* comment-only).
- Gateway catalog match confirmed live: gemini-3.8-flash=1048576,
  vertex/gemini-3.8-flash=1048576, deepseek/deepseek-flash=1048576.
- 5 `test_registry_render.py` failures are an expected cross-lane dependency
  on L1-beta updating `catalog/ai-registry.json` (not a combos defect).
- No test files changed in the commit (diff vs d08f7f2 empty for tests/).
- Risk: `antigravity/claude-opus-4-6-thinking` not in live /v1/models (0
  antigravity/* models); combo store uses computed_context_length=1048576.

### P0 (d88ed3b) — REJECTED by both reviewers

- All code invariants passed (idempotency, read-modify-write, env-relative
  paths in scripts, knob ordering, shim .cmd resolution, loud failure on
  missing shim, documented deferral of apply.ps1 fix).
- **Blocking defect:** `docs/handoff/2026-09-30-laneP0-admission.md:4`
  contains hardcoded username path
  `C:\Users\<user>\AppData\Roaming\npm\node_modules\omniroute` — violates
  AGENTS.md Hard Rule 1. Both reviewers independently identified the same
  file:line. The defect is in a doc file, not in the launcher scripts (scripts
  correctly use `$env:APPDATA`).
- P0 branch tests: 160 passed, 0 failed.

## Admission / rate-limit log

| Event | Count |
|---|---|
| `chat_admission_busy` | 0 |
| `Rate limit exceeded` | 0 |
| Refusals | 0 |

No admission or rate-limit errors were encountered during any reviewer spawn.
No refusals were issued by any reviewer.

## Recommended follow-ups (for a follow-up lane — not fixed here)

1. **P0 blocking (High):** Redact hardcoded username `<user>` from
   `docs/handoff/2026-09-30-laneP0-admission.md:4`. Replace with
   environment-relative path. L1-beta lane fix.
2. **P0 deferred shim (Medium):** Mirror `start-stack.ps1` shim fix to
   `apply.ps1:96-97` (still uses bare `omniroute`).
3. **Combos test dep (Expected):** 5 test failures resolve once L1-beta
   updates `catalog/ai-registry.json` `context_advertised` values.
4. **Combos unverifiable model (Low):** `antigravity/claude-opus-4-6-thinking`
   absent from live gateway; monitor for retirement/rename.

## Files committed

- `docs/handoff/2026-09-30-laneReview-wave1.md` — full review findings
- `logs/handoff-sessions/DONE-ws-review-wave1.md` — this file

## Constraints honored

- Review worktree cut from main d08f7f2 — no push, no merge, no rebase,
  no checkout of other lanes' branches.
- Other lanes' commits read via `git show <sha>` only.
- No fixes applied — all defects recorded precisely for a follow-up lane.
- Gateway probe script read the omniroute client key from
  `configuration/api-keys.yml` without ever printing the key value.
