# AutoOS plan 2026-10 (autoos-L1-main, routing D-643 step 4)

Source: operator input 2026-10-08 (routing repo), audit `docs/plans/autoos-audit-2026-10-08.md`, routing decisions
D-645/D-646/D-649/D-653, workstation RS-GEMINI-PARAMS. Lanes run in this order; a lane starts when its `after` lanes are
merged and the operator answers it depends on (Q-ids, `docs/plans/autoos-questions-2026-10-08.md`) are in.

Conventions for every lane
- Owner layer: L1 = autoos-L1-main coordinates + merges; L3 = spawned writer/reader via autoos-agent (never hand-rolled).
- Tier order for L3 legs: trial (OVH, Vertex) -> free -> credits (ainative to 11-02, nscale, together, kilo) -> paid.
  OVH only for single-task implementation legs (no prompt cache). No deepseek, no paid muse; zen
  `muse-spark-1.3-contributor-free` is a free leg (routing 10-08).
- Review: writer + 2 seats from 2 non-writer families (R-orch-13); state-mutating/installer lanes add a Sonnet final (R-orch-10).
- DONE = merged to main by L1 with `tools/prepush.py` green for the exact sha, CI green on main, plan-graph item closed.
- Output: terse (D-649); every lane and sub-task is a plan-graph item (batch: routing docs/plan/graph-updates-2026-10-08-autoos.md).

| # | Lane | Owner / tier | After | DONE criteria | TEST that proves it |
|---|---|---|---|---|---|
| 1 | REDMAIN: layer-rename fallout (tests, default profile `omniroute-l1-orchestrator` in install.sh/.psm1, ai-stack verify combo, probe-sweep prefix), wire test_probe_ledger.py | L3 writer qoder; seats 2 families | - | CI run on main green all shards | CI; `python3 tests/test_suite_wiring.py`; `bash tests/run-tests.sh` filters for 34-ai-services, 05, 33 |
| 2 | R1 CENTRAL RE-APPLY (P1): render host opencode config from catalog, `ai-stack.sh init`, `omniroute/apply.sh --prune` on the central gateway (prox applies) | spec L1, apply prox via routing | 1 | gateway lists l1/l2/l3 combos; t* pruned only after host config drops them | spawn probe card `review/trivial` -> rc 0 on route `l3-driver`; `oc_status` both lanes ok |
| 3 | HOSTMIRROR (R-router-10 mechanical): spawner preflight refuses a route whose combo is not declared in host opencode config AND listed by the gateway, naming the fix | L3 writer (trial/free), 2 seats + Sonnet final | 1 | refusal message names missing id + fix command | unit test with stale host config fixture; live: spawn with a removed combo -> refused before launch |
| 4 | TERSE (D-649): rules into SKILL.md top + main-orchestrator.md; spawner adds a fixed terse-output prefix to every spawned prompt + output cap per role (registry field) | L3 writer, 2 seats | 1 | prefix present in every spawned prompt; cap enforced per role | unit test on prompt assembly; live probe: spawned output line count <= cap |
| 5 | R2 WORKSTATION CONFIGS: central-first provider + local fallback, drop direct meta/deepseek decls, install.sh meta block behind an explicit flag | L3 writer; workstation applies via workst-L0 | 2, Q1 | workstation user config lists central first; no meta/deepseek provider | workstation probe: request via central OK; stop central -> local answers |
| 6 | RS-GEMINI-PARAMS: thinkingLevel mapping (low/medium/high, none->minimal), strip temperature/top_p/top_k on gemini/vertex legs, revert path, upstream issue/PR; openhands config.toml sampling on gemini combos | L3 writer (free), 2 seats + Sonnet final (gateway patch) | 2 | patch + revert script + test; upstream issue filed | unit test on request transform; live probe on a vertex leg returns 200 with thinkingLevel set |
| 7 | DENY-LEGS (combo change, wait for server-L1-routing go after operator OK): deepseek + paid muse out of every default route; trial legs first in l2-worker/l3-driver; OVH off any agent that outlives one task (`prompt_cache` registry field + select_combo check) | L3 writer, 2 seats + Sonnet final | 2, Q2, D-653 go | renders contain no deepseek/paid-muse leg; orchestrator/track agents only on caching legs | `registry.py render --check`, `test_registry_render.py`, new test: no route for role orchestrate/track contains a `prompt_cache:false` leg |
| 8 | DEAD-ROWS: scw/* rows + capability-overrides, navy/bluesminds/arcee `available:false` with reasons, retire `spend=credit` or implement it | L3 writer (free), 2 seats | 1, Q7 | registry has no unused live rows; `spend` field either enforced or rejected | `registry.py validate`; card with `spend=credit` -> refused or routed to a credit leg (test) |
| 9 | LAYERED-PLANNING (D-646): move plangraph `skills/plangraph-planning` (c463190) to `.agents/skills/layered-planning/`; generic; graph mirroring one section; reference superpowers planning skills, never restate; plangraph-L1 fixes (aliases not pinned ids; layer/role/agent_id + kinds marked "planned (plangraph MODEL-KINDS)"; task-size contract provisional); list in repository-index; reference from main-orchestrator.md + swarm-orchestration; plangraph keeps a 3-line pointer | L3 writer, 1 seat | 1 | skill passes `tools/skill-rules.py check`; links resolve | `python3 tools/skill-rules.py check`; link check; plangraph-L1 confirms pointer |
| 10 | SKILL-RULES (operator "further suggestions", see below): reword R-orch-29, checkpoint-file rule, spawn budget, token/context metrics, canary, registry ack budgets | L3 writer, 2 seats + Sonnet final | 3, 4, Q3 | rules in SKILL.md each with a source; mechanical parts in code | `tests/test_skill_rules.py`; per-part unit tests listed below |
| 11 | c2 OC-RUNTIME: watcher/throttle/inbox from `docs/ai/oc-runtime-sources/*.txt` into `tools/oc_runtime/` with tests; canary retry; heartbeat refresh | L3 writer (OVH ok), 2 seats + Sonnet final | 1 | runtime runs from repo; .txt copies deleted | unit tests per module; live: kill a lane -> watcher restarts it without killing spawned runs |
| 12 | PROVIDER DOCS: `docs/api-keys.md` Vertex ADC + service-account steps, OVH AI Endpoints key steps, no-key providers, together/navy/bluesminds/arcee status; no secrets, no hostnames | L3 writer (free), 1 seat | 8 | sections present | `tests/linux/33-documentation.sh`; secret/hostname grep |
| 13 | LITELLM-FALLBACK: re-render config.yaml chains from the registry; automatic OmniRoute -> LiteLLM switch | L3 writer, 2 seats + Sonnet final | 7 | chains generated, `fallbacks` non-empty | render check test; live: gateway stopped -> request served by LiteLLM |
| 14 | PROD-TEST L0->L3 (item 11): OpenCode replica router -> lead -> phase -> worker on one real small task; record PID growth, dead subagents, inter-agent messages, combo latency, credential canary | L1 drives, L3 legs; cap $6 | 2, 3, 4, 11 | record file with all metrics + task merged | the run itself; record checked against thresholds (no orphan PIDs, canary never echoed, all messages delivered) |
| 15 | GROK temporary combo (own combo, no fallback, retries 0, timeout > 700 s) | L3 writer | Q4 | combo present, used only after free legs rate-limit | render check + live probe |
| 16 | 400-LINE CAP phase 1: split `tools/autoos-agent.py` (11.5k lines) by concern | L3 writers per module, 2 seats each | 3, 4, 10 | no behaviour change; file count up, max file < 2k (phase 1) | full suites green before/after; spawn smoke |

Out of AutoOS scope (owner elsewhere, tracked only): server self-healing MCP (item 19, server-L1/prox, T6-HEAL pilot);
Open WebUI + HA STT/TTS (item 16, server-L1, T6-OWUI/T6-HA); combo proposal by AA index (server-L1-routing D-653).

## Proposed unattended-orchestration skill changes (operator "further suggestions")

| Suggestion | L1 verdict | Change |
|---|---|---|
| Dumb deterministic router | already true (`autoos_routing.select_combo`, one path CLI+MCP) | keep; generate the prose routing table from `select_combo --explain-all` (check in CI) instead of hand copies |
| Tier by reasoning, not window | adopt (context-rot evidence; code already gives L2 128k) | reword R-orch-29: 1M band only when live context > 128k is unavoidable; default = split into bounded tasks + file merge (Q3) |
| In-session checkpoint at ~60-70 % fill | adopt | new R-coord rule: at 60-70 % fill rewrite the state file (goal, DONE criteria, deny-lists, decisions verbatim); `heartbeat` reports fill and flags > 70 % without a fresh state file (mechanical) |
| Spawn budget per run | adopt | spawner counts children per run (registry `spawn_budget` per role); refuse past budget; covers qwen/gemini/codex horizontal fan-out |
| Per-run token/context metrics | adopt | `exit.json` gains `tokens_in/out`, `context_peak`; needed before tuning checkpoint and watchdog thresholds |
| Canary key-string | adopt | tracked fake key-shaped string; post-run leak check alerts if any output contains it (turns the grep fence into a tested boundary) |
| Registry-held ack budgets | adopt | `ack_budget_tokens` per reasoning combo in the registry; prose number removed |
| `spend=credit` dead field | adopt | implement (route to credit legs) or reject at card parse (lane 8) |
| Watchdog thinking-trace signal | adopt later | threshold per combo from measured median decode time once metrics exist (after lane 10) |
| `--lean` x writer as routing input | adopt | `select_combo` excludes client x role pairs that cannot honour lean, instead of refusing at spawn |
| Clean-clone assert on respawn | adopt | respawn path logs uncommitted writes of the killed run and refuses reuse of a dirty clone |
| Max ~2 levels deep | keep as is | depth budget already 2; deeper fan-out goes through Workflow scripts, not more levels |
| D-649 terse output, peer talk, every task a graph item | adopt (operator mandatory) | SKILL.md top + main-orchestrator.md + spawner prefix/cap (lane 4) |
