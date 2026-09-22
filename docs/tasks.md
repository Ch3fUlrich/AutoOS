# Tasks — live run board (tier1 owns this file)

One row per track. tier1 adds the row at spawn, updates Status at
reconcile. DONE-criteria must be checkable (`ack` probe, suite counts,
`git grep` proof). Logs linked under `logs/`.

| Task | Owner | Started | DONE-criteria | Status |
|---|---|---|---|---|
| 3-tier close-out (tracks A/B + reconcile) | tier1 | 2026-09-20 | 264 PS / 107 sh green, IP-clean, handoff updated | Done 2026-09-20 |
| Tier enforcement (`agents` block + tests) | tier1 | 2026-09-20 | PS + sh tier-enforcement cases green | Done 2026-09-20 (280 PS / 108 sh) |
| MCP-everywhere + spark combo + cohere rag + proxy repair (tracks A/B) | tier1 | 2026-09-22 | serena memory off via harness field, spark combo + rag combo live-acked, litellm starter + master alignment + strict-drop, git/Zed permission fixes live-proven | Done 2026-09-22 (PS + sh green, see handoff) |
| Skill: watchdog clause + runtimes section | tier1 | 2026-09-20 | skill renders, link check green | Done 2026-09-20 |
| `docs/tasks.md` board itself | tier1 | 2026-09-20 | this file tracked, linked from handoff | Done 2026-09-20 |
| OpenHands runbook + 3-service resume | tier2-E | 2026-09-20 | runbook zero-context, start-stack + autostart resume serve, tests assert | Done 2026-09-20 |
| Tier reshape (spark-only t1, paid-only clean, #high) | tier1 | 2026-09-20 | spark-only + paid-only tests green, dual-lens APPROVE | Done 2026-09-20 (281 PS / 108 sh) |
| Cheap-inference legs + retry trim (≈3) | tier1 | 2026-09-20 | cinf legs in simulate, 286 PS green, config.toml 5→3 | Done 2026-09-20 |
| Push to main | tier1 | 2026-09-20 | BLOCKED: remote main is 64 commits ahead (USB/rescue + llm-models work since 2026-09-17); pushed feature branch instead | Done 2026-09-20 → `tier-orchestration-2026-09-20`, needs PR + rebase onto main |
| `apply -Probe` re-date verification table | — | — | 6 combos ack NEW legs, verification.md re-dated | Queued |
| Web Router-tiers backend (apply/switch) | — | — | endpoints + tests, card no longer display-only | Queued |
| Agent Canvas re-evaluation | — | — | LLM-profile proof on current image | Queued |

## App comparison (why a file, not a new app)

| Surface | Shows sessions/tasks? | Verdict |
|---|---|---|
| OpenHands `:3000` | current sandbox conversation only, no task queue | run UI, not a tracker |
| `opencode serve :4096` | session list (`/api/sessions`, auth-required) | partial — sessions, not DONE-state |
| AutoOS `--serve :8777` | `/api/state` + `/api/log` progress while running | partial — live, not durable |
| This file | Running / Queued / Done + owner + DONE-criteria | the ledger — git history is the audit trail |
