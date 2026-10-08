# Operator questions 2026-10-08 (autoos-L1-main, routing D-643 step 5)

Routing asks these as multiple choice. Recommended default first. Lane numbers refer to `autoos-plan-2026-10.md`.

| Q | Question | Options (default first) | Blocks |
|---|---|---|---|
| Q1 | Which key does the workstation use for the central gateway (central-first, local fallback)? | (a) new named workstation key; (b) reuse the coding.vm key; (c) tailnet only, no central | lane 5 |
| Q2 | L1/L0 model + effort: D-637 says L1 = Opus 5.5 medium; item 15 says L1 high effort, L0 Sonnet low/medium. Which holds? | (a) L1 Opus medium, L0 Sonnet low/medium (D-637 + item 15 L0); (b) L1 Opus high, L0 Sonnet; (c) keep D-637 for both | lanes 7, 10 |
| Q3 | Tier contract: replace "L1/L2 = 1M window" (R-orch-29) with "strongest reasoning + file memory; 1M only when > 128k live context is unavoidable"? | (a) reframe; (b) keep 1M-or-nothing | lane 10 |
| Q4 | Grok bot wrapper code: how does it reach a repo? | (a) paste into the routing repo; (b) gist; (c) skip Grok | lane 15 |
| Q5 | Fleet-bus relay port 47100 between coding.vm and workstation (Tailscale ACL)? | (a) git-based message dir until you open it; (b) open it now; (c) later | lane 11 |
| Q6 | Second OpenCode Free account? | (a) read the terms of service first; (b) one account only; (c) two | none (cost) |
| Q7 | navyai, bluesminds, arcee have no free models: mark `available:false` with reason, or delete rows? | (a) `available:false`; (b) delete rows | lane 8 |
| Q8 | Rules "Done notes name the model", effort policy, 400-line cap: where do they live? | (a) skill only; (b) AGENTS.md too | lanes 10, 16 |
| Q9 | l2-orchestrator head: repo has `agy/claude-sonnet-5-5-medium` first, server render drops it for privacy (`[vertex, deepseek]`). Which? | (a) drop agy-sonnet head, vertex first (deepseek removed by lane 7); (b) keep agy-sonnet head | lane 7 |
