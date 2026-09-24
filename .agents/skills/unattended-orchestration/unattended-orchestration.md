# Unattended orchestration — 3-tier protocol (AutoOS skill)

How to run long-horizon agent work cost-efficiently through OmniRoute,
and how orchestration hands off without losing the goal.

Status: living doc. Proven 2026-09-20: all 6 combos live-probed `ack`
(`t1-orchestrator`→openrouter spark-contributor, `t1-orchestrator-clean`→openrouter spark,
`t2-worker`→gpt-oss-120b, `t3-driver`→mistral-code-latest). Gateway :20128.

## Tier enforcement (mandatory depth, runtime-checked)

Only `t1` may sub-orchestrate, only `t2` may review-spawn, `t3`
spawns nothing. This is **hard-enforced by `opencode.jsonc`**, not a
prompt convention — the `subagent` permission action (V2 docs:
`agents` + `permissions` guides) denies everything first, then allows
exactly one child per level:

- `t1-orchestrator` (`mode: all`, model `omniroute/t1-orchestrator`): deny
  `subagent *`, allow `subagent t2-worker`.
- `t2-worker` (`mode: subagent`, model `omniroute/t2-worker`): deny
  `subagent *`, allow `subagent t3-reviewer`.
- `t3-reviewer` (`mode: subagent`, model `omniroute/t3-driver`): deny
  `subagent *` (no allow rule — leaf).

Effect: a t2 that tries to spawn another t2 (or a t3 that
tries to spawn anything) is **denied by the runtime before it runs**.
The suite asserts all three agents exist with exactly this shape
(`tests/run-tests.*`: tier-enforcement case). Two limits to know:

1. The `agents` block binds inside the opencode runtime (CLI/TUI/
   `serve`). Harness or API callers that address `omniroute/t*-*`
   models directly bypass agents and must follow the same depth by
   convention — the skill prompt states the spawn budget explicitly.
2. A subagent uses its configured model, so depth and model stay
   paired: t1-orchestrator always runs on `omniroute/t1-orchestrator`,
   t2-worker on `omniroute/t2-worker`, t3-reviewer on
   `omniroute/t3-driver`. Ask for a `-clean` twin when the task is
   sensitive (same depth, no-training legs).

## Stuck-agent watchdog (t1 probes, never waits forever)

T1 owns liveness. Every background t2 gets a heartbeat line in
`logs/orch-<date>.log`; t1 re-checks on a fixed interval and treats
silence as stuck — with one exception for rate limits (below):

1. **Probe every 5 minutes**: t1 checks each live t2 for a fresh
   heartbeat or report line. No new output in 15 minutes = stuck.
2. **Rate-limit ≠ stuck**: a `429 / quota / budget` message with a
   retry timestamp resets the clock — the agent is waiting, not dead.
   Only silence with no rate-limit trace counts as stuck.
3. **Kill + re-spawn once per track**: t1 terminates the stuck
   t2 and respawns it with the same track + DONE criteria (max 3
   respawns per track, then the track is marked blocked in
   `docs/tasks.md`).
4. **Never wait forever**: t1 always sets a wall-clock deadline per
   track; on expiry it reconciles whatever reported and records the
   rest as open in `docs/handoff.md`.
5. **Log it**: every probe, kill, and respawn appends one line to
   `logs/orch-<date>.log` so the next t1 can audit what happened.

## The tiers (ids are a contract — never rename, only relabel)

| Tier | Role | Context | Chain head (free-first) |
|---|---|---|---|
| `t1` | orchestrator: plans, slices, verifies, never codes directly | 1M only, spark-only + `#high` (xhigh effort) | zen spark-contributor-free → openrouter spark-contributor → zen spark (paid). No gemini-3.1-pro (reasons worse than 3.8-flash). |
| `t1-orchestrator-clean` | same, no prompt-training legs (sensitive data) | 1M, paid legs only | openrouter plain spark → zen paid spark. No free legs (any big free model may train). |
| `t2` | smart worker: reasoning, reviews, mid-size codegen | ≤128k | gemini-3.8-flash → groq/cerebras/sambanova gpt-oss-120b → cheap-inference deepseek/glm/kimi → openrouter/deepseek flash |
| `t2-worker-clean` | same, paid legs only (sensitive data) | ≤128k | deepseek direct → openrouter/zen flash → mistral-small direct. No free legs, no reseller legs. |
| `t3` | cheap driver: small edits, probes, parallel reviews | ≤128k | mistral-code → groq/cerebras qwen3.8-27b → cheap-inference glm-4.5-air/minimax-m2.7 → mistral-small → deepseek/zen flash |
| `t3-driver-clean` | same, paid legs only (sensitive data) | ≤128k | deepseek direct → mistral-small direct → zen paid flash. No free legs. |

Rule: anything under 1M context belongs in t2/t3, never t1
(`opencode.jsonc` limits enforce this: 1M vs 128k). Small-context models
CAN sub-orchestrate — slice their tasks small instead of promoting them.

## The 3-tier run pattern

```
t1 (1 orchestrator)
  plans the goal, slices into tracks, defines DONE per track
  ├─ t2-A (sub-orchestrator, background): owns tracks 1..n
  │    ├─ t3-a1 (reviewer, lens X, e.g. mistral/qwen leg)
  │    └─ t3-a2 (reviewer, lens Y, e.g. deepseek leg)
  └─ t2-B (sub-orchestrator, background): owns tracks n+1..m
       ├─ t3-b1 (reviewer, lens X)
       └─ t3-b2 (reviewer, lens Y)
```

Rules:

1. **Max fan-out 2+2+2** (1 t1 → ≤2 t2 → ≤2 t3 each). Prevents
   quota collapse and keeps reviews reconcilable.
2. **Different lenses per t3 pair**: one cheap-codegen leg
   (`t3-driver`/`t3-driver-clean`, mistral/qwen) + one smart leg
   (`t2-worker`/`t2-worker-clean`, gpt-oss/deepseek). Different models find
   different bugs; identical reviewers are wasted tokens.
3. **Rate-limit hygiene**: probes are `Reply with exactly: ack` +
   ONE focused review question each, small output budget. Reasoning
   models (spark) need a real budget (≥2048 tokens) or they return
   empty — that is a budget fault, not a routing fault.
4. **Reviews run in parallel**, reconciled by the owning t2 before
   reporting up. T1 never gets two conflicting reviews directly.
5. **Verify, don't trust**: every t2 runs its own tests
   (`tests/run-tests.ps1`, `tests/run-tests.sh`) and one live `ack`
   probe per touched tier before reporting.
6. **Non-overlapping files**: t1 assigns tracks with disjoint file
   sets (e.g. A = configuration/remote/watchdog, B = catalog/combos/UI)
   so parallel workers never merge-conflict.

## Long-horizon continuation (the main-agent handoff)

A t1 orchestrator that finishes its big part does NOT go idle:

1. Write the goal + plan + DONE-state to `docs/handoff.md`
   (what is proven, what is open, key commands, known issues).
2. Spawn (or ask the user to spawn) a NEW t1 with the handoff as
   its first input. The new main agent re-plans only the OPEN items.
3. The old session ends with: files changed, test output, phone URLs,
   remaining gaps — same shape as this doc's reports.

This is what makes multi-stage goals resumable across sessions,
reboots (see autostart), and even machines.

## Remote + fallback matrix (phone-reachable today)

Machine: Tailscale + LAN IPs are machine-local — find yours with
`tailscale ip -4` / `ipconfig` (Windows) or `hostname -I` (Linux).
Never commit them here (repo is public); `<tail-ip>` below means
your Tailscale IPv4.

| Path | URL (phone via Tailscale) | Role |
|---|---|---|
| OpenHands | `http://<tail-ip>:3000` | PRIMARY agent UI (t1 inside) |
| opencode serve | `http://<tail-ip>:4096` | FALLBACK if OpenHands breaks (`opencode serve --hostname 0.0.0.0 --port 4096`) |
| OmniRoute | `http://<tail-ip>:20128` | router for Zed/Desktop/nvim (client key via env, never URL) |
| AutoOS `--serve` | loopback only unless elevated | installer UI; LAN bind needs `netsh http add urlacl url=http://+:8777/ user=<you>` from an elevated shell, then `setup.ps1 -Serve -Bind 0.0.0.0` |

Fallback order when something breaks: OpenHands → opencode serve →
Zed (autoos-omniroute provider) → opencode desktop → nvim+sidekick
(`<leader>aa`). Every leg routes through :20128, so a single client
key restores all of them. Watchdog: `configuration/healthcheck.*`
(probe :20128/:3000/:4096/:8777, `--fix` restarts via start-stack).

## Where subagents run: opencode vs OpenHands

Unattended orchestration spans **two runtimes**, not one:

- **opencode runtime** (this repo's harness): t1/t2/t3 agents
  above, spawned via the `subagent` tool with `omniroute/t*-*` models.
  Fast, cheap, file-scoped — this is where the 3-tier pattern executes.
- **OpenHands** (`:3000`, `openhands-app` container): the durable
  multi-project agent UI. One container per checkout today
  (`start-stack.ps1 -App openhands`); run a second container per extra
  project (separate port + `~/.openhands` mount). OpenHands is the
  PRIMARY phone UI and the place long autonomous runs live; opencode
  `serve` (`:4096`) is its fallback, not its replacement.
- **Agent Canvas** (port 8000, beta): upstream's multi-backend control
  center (`PROJECTS_PATH` + `~/.openhands` mounts). Verdict 2026-09-20
  (docs/verification.md): stay on `:3000` until Canvas leaves beta —
  then it becomes the multi-project answer and this section gets
  rewritten with its LLM-profile proof.

## Cost discipline

- Default model is `omniroute/t1-orchestrator` (free legs first, paid overflow
  only from sanctioned providers). Paid Zen legs 402 without balance —
  the chain hops, by design.
- `auto/*` combos are the zero-setup bootstrap; strict `t*-*`
  combos are the deterministic path for unattended runs.
- Sensitive data → `-clean` twins, always. When in doubt, `-clean`.

## Pipeline improvements (2026-09-20)

Proven this session by a 1→2→4 run (t1 → t2-A/B → dual t3
reviewers each): disjoint file sets per track (A = watchdog/resilience,
B = catalog/UI/pipeline), parallel dual-lens reviews reconciled by the
owning t2, one live `ack` probe per touched tier.

- **Disjoint file sets**: t1 assigns non-overlapping tracks so parallel
  workers never merge-conflict.
- **Parallel dual-lens reviews**: one cheap-codegen leg
  (`t3-driver`/`t3-driver-clean`, mistral/qwen) + one smart leg
  (`t2-worker`/`t2-worker-clean`, gpt-oss/deepseek). Reconciled by the owning
  t2; t1 never sees two conflicting reviews directly.
- **Ack-probe budget**: reasoning models (spark) need ≥2048 output
  tokens or they return empty — a budget fault, not a routing fault.
  Keep probes at `Reply with exactly: ack` + one focused question.
- **Handoff rotation**: a t1 that finishes its big part writes goal +
  plan + DONE-state to `docs/handoff.md` and a NEW t1 re-plans only
  the OPEN items. Resumable across sessions, reboots, machines.
- **Watchdog auto-resume + human fallback ladder**: `healthcheck.*`
  probes :20128/:3000/:4096/:8777 log-only; `--fix`/`-Fix` resumes via
  `Start-AutoOSStack`. Ladder: OpenHands → opencode serve → Zed →
  Desktop → nvim+sidekick, all through :20128 with one client key.
- **Next gaps**: `--fix` should also resume `opencode serve :4096`
  when down (currently only gateway + container); the web Router-tiers
  card is display-only until backend apply/switch endpoints exist;
  `docs/README.md` still lacks the unattended-orchestration row.
