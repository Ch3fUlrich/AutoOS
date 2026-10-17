# DONE — ws-incident (2026-09-30)

Lane INCIDENT-DOC completed. Operator directive: document failures, so future agents will not do this again. Gateway is healthy again (creds re-entered). Documentation + one guard rule; no gateway changes.

## Summary

- **Verdict:** documentation complete; one new skill rule (R-coord-14) added and validated.
- **Branch:** `L1-backlog/ws-incident-20260930` (no push, no merge)
- **Skill-rules check:** `ok: 42 rules`

## Commits

- `fa39c6a` — doc(handoff): storage-key incident 2026-09-30 + gateway findings #8-#14 + R-coord-14

## Files

- `docs/handoff/2026-09-30-storage-key-incident.md` (new) — the big write-up
- `.agents/skills/unattended-orchestration/SKILL.md` (rules R-coord-12, R-coord-13, R-coord-14 added to coord section)
- `docs/handoff/2026-09-30-omniroute-gateway-findings.md` (findings #8-#14 appended; #7 kept in original position)
- `logs/handoff-sessions/DONE-ws-incident.md` (this file; git-ignored, committed with -f)

## Reviewer

- t3-reviewer leaf (ses_f0d47fa4fffeceqQNhd09AOEld): initial verdict FAIL.
- Fixes applied: redacted username in abs path (%APPDATA%\npm\omniroute.cmd), completed truncated doc, moved R-coord-14 to coord section (was in orch), added R-coord-12/13 from main checkout, fixed wrong claims (999→400, aamaan→open), added missing evidence (admission knob in .env, restart time 13:39:12Z, 28 rotated, test-all green, DB intact, lane denial, commit d88ed3b + branch, apply.ps1 follow-up), replaced placeholder hashes with file-content evidence, dropped generic prevention rules.

## Open Items (documented as open)

- Deepseek `reasoning_text` 400 (thinking mode)
- Vertex leg 400 during gemini-leg cooldowns
- Scaleway qwen leg 400 (max_completion_tokens clamp)
- Fast failover away from rate-limited legs (operator request)
