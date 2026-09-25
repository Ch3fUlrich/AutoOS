# Unattended orchestration — 3-tier protocol (AutoOS skill)

How to run long-horizon agent work cost-efficiently through OmniRoute,
and how orchestration hands off without losing the goal.

Status: living doc. Proven 2026-09-20: all 6 combos live-probed `ack`
(`tier1`→openrouter spark-contributor, `tier1-clean`→openrouter spark,
`tier2`→gpt-oss-120b, `tier3`→mistral-code-latest). Gateway :20128.

## Tier enforcement (mandatory depth, runtime-checked)

Only `tier1` may sub-orchestrate, only `tier2` may review-spawn, `tier3`
spawns nothing. This is **hard-enforced by `opencode.jsonc`**, not a
prompt convention — the `subagent` permission action (V2 docs:
`agents` + `permissions` guides) denies everything first, then allows
exactly one child per level:

- `tier1-orchestrator` (`mode: all`, model `omniroute/tier1`): deny
  `subagent *`, allow `subagent tier2-worker`.
- `tier2-worker` (`mode: subagent`, model `omniroute/tier2`): deny
  `subagent *`, allow `subagent tier3-reviewer`.
- `tier3-reviewer` (`mode: subagent`, model `omniroute/tier3`): deny
  `subagent *` (no allow rule — leaf).

Effect: a tier2 that tries to spawn another tier2 (or a tier3 that
tries to spawn anything) is **denied by the runtime before it runs**.
The suite asserts all three agents exist with exactly this shape
(`tests/run-tests.*`: tier-enforcement case). What the runtime needs,
measured live against opencode 2.0.16 (2026-09-24):

1. **Depth is `experimental.subagent_depth`.** A top-level
   `subagent_depth` is dropped as an "unsupported legacy setting" and the
   depth defaults to 1 - tier2 then answers "Subagent depth limit reached
   (1)". Both suites gate the key.
2. **The reviewer is fenced by `shell` rules and MCP denies**, not by
   `edit`/`write` alone. v2 calls the shell action `shell` (a `bash` rule
   matches nothing), and serena's `create_text_file`/`replace_content`
   write files past an `edit` deny. tier3-reviewer therefore denies
   `serena_*` except a read-only allow list, the omnigraph write tools and
   `playwright_browser_run_code_unsafe`, and carries every
   `catalog/agent-harness.json` fence (`bash_deny_all` + `bash_deny_leaf`:
   no commit, push, checkout, reset, env dumps) as a `shell` deny.
3. **Children keep their model; the entry agent does not.** A child
   spawned through the subagent tool runs on its own agent's model. But
   `opencode run --agent tier2-worker` runs on the top-level default
   (`omniroute/tier1`) unless `--model` is passed too - use
   `tools/autoos-agent.py` (below), which always pairs them.
4. **The subagent tool accepts a `model` override**, so a model can still
   pick another model at spawn time; the agent prompt, not the runtime,
   keeps it on its tier.
5. **Harness or API callers that address `omniroute/tierN` directly**
   bypass the agents and follow the same depth by convention only.

## Spawning - one command, any client

```bash
python3 tools/autoos-agent.py list                            # tiers, card fields, client matrix, depth
python3 tools/autoos-agent.py run "Add a test for X"          # empty card -> tier2
python3 tools/autoos-agent.py run --card role=review --lean "Review lib/linux/ui.sh"
python3 tools/autoos-agent.py run --card privacy=sensitive "..."   # -> tier2-clean
python3 tools/autoos-agent.py run --client qwen --card complexity=trivial "..."
python3 tools/autoos-agent.py run --client claude --joinable --title d1 "..."
python3 tools/autoos-agent.py run --tier 2 --isolate "Add a test for X"   # tier by hand
python3 tools/autoos-agent.py run --tier 1 --free "..."       # no key, no gateway, no spend
python3 tools/autoos-agent.py run --card role=review --dry-run "..."   # print the plan only
```

For opencode it passes the agent's own model, runs `--standalone` (the
background opencode service keeps the environment it started with, so a key
exported later is ignored), closes the child's stdin (with an open pipe
`opencode run` waits for more prompt text and never starts), reads the client
key from `AUTOOS_OMNIROUTE_KEY` or `configuration/api-keys.yml` without
printing it, and logs one line per run to `logs/orch-<date>.log`: client,
card, combo, reason, routing version, depth.

### The task card (routing v1, [ADR 0006](decisions/0006-launch-time-routing-resolver.md))

Without `--tier`, `autoos_routing.select_combo` picks the combo. The CLI and
the MCP server both call it, so there is only one routing decision. Unknown
fields or values are an error. The steps are filters first (privacy, ctx,
spend), then a preference by role and complexity.

| Field | Values | Default |
|---|---|---|
| `role` | orchestrate, implement, review | implement |
| `complexity` | trivial, standard, hard | standard |
| `ctx` | 128k, 1m | 128k |
| `privacy` | public, sensitive | public |
| `spend` | free-ok, credit | free-ok |

| privacy | ctx | role / complexity | spend | combo |
|---|---|---|---|---|
| public | 1m | any | any | `tier1` |
| public | 128k | orchestrate, or hard | any | `tier1` |
| public | 128k | implement / standard | free-ok / credit | `tier2` / `tier2-credit` |
| public | 128k | review, or trivial | free-ok / credit | `tier3` / `tier3-credit` |
| sensitive | 128k | review, or trivial | any | `tier3-clean` |
| sensitive | 128k | anything else | any | `tier2-clean` |
| sensitive | 1m | any | any | refused - split to 128k, or `--allow-training` (logged) |

The combo's tier picks the opencode agent (`tier3-clean` runs `tier3-reviewer`).

### Clients

| Client | Headless | Gateway | Native sub-agents | Auth | Command |
|---|---|---|---|---|---|
| opencode | yes | yes | yes | `AUTOOS_OMNIROUTE_KEY` | `opencode run --standalone --agent … --model omniroute/<combo>` |
| claude | yes | no | yes | `claude` login (subscription) | `claude -p`; `--joinable`: `claude --bg --remote-control <title>` |
| qwen | yes | yes | yes | `AUTOOS_OMNIROUTE_KEY` | `omniroute run qwen --model <combo> -- -p …` |
| gemini | yes | yes | no | `AUTOOS_OMNIROUTE_KEY` | `omniroute run gemini --model <combo> -- -p …` |
| codex | yes | yes | no | `AUTOOS_OMNIROUTE_KEY` | `omniroute run codex --model <combo> -- exec …` |
| agy | yes | **no** | no | `agy` login (own Google account) | `agy -p …` |
| qoder | yes | **no** | yes | `qodercli login` (own account) | `qodercli -p …` - promo, `privacy=public` only |

agy and qoder cannot use the gateway: they bill their own accounts, and each
needs one interactive login on the machine first. `list` shows which clients
are installed and when qoder last worked (stale after 7 days). Permissions
map onto each CLI's own modes. `role=review` runs read-only (plan mode or a
read-only sandbox). Otherwise file edits are approved and anything else asks.
`--no-auto` keeps the CLI's own prompting.

### Depth, lean, isolation

- **Depth budget.** Every child gets `AUTOOS_AGENT_DEPTH` (parent + 1) and
  `AUTOOS_AGENT_MAX_DEPTH` (default 2, the same as opencode's
  `experimental.subagent_depth`). A spawn past the max exits with code 4.
  `--max-depth` can only lower the inherited max. This budget is the only
  depth control qwen, gemini, codex, agy and qoder have. Inside one opencode
  process, nesting is still `experimental.subagent_depth`.
- **`--lean`** (opencode, claude) starts no serena, playwright or context7,
  which is right for research and review agents. For opencode it overlays
  each full server entry with `disabled: true`. `enabled: false` is not an
  opencode 2.x field: it is dropped without a warning and the server starts
  anyway. Measured peak process-tree RSS of one run: 1406 MB → 678 MB.
  claude uses `--strict-mcp-config`.
- **`--isolate`** runs the agent in a private `git clone --local` on its own
  branch. For opencode it also gets its own data dir and a deny on every path
  outside the clone. **Not a git worktree:** opencode resolves a worktree to
  the main checkout, and a worker's writes from inside one landed in the main
  repo. Take results with `git fetch <clone> <branch>`; nothing is merged or
  deleted for you. The clone starts from `HEAD`, so commit first.
- **`--free`** maps every tier to opencode's own free model (default
  `opencode/big-pickle`) through `OPENCODE_CONFIG_CONTENT`. Use it to
  exercise the chain and the fences before any provider key exists. Free
  promo models may train on prompts, so `--free --clean` is refused.

### Over MCP (OpenHands, Claude Code, opencode, Zed)

`tools/autoos_agent_mcp.py` is a stdio MCP server
(`uv --quiet run --no-project --with 'mcp<2' python tools/autoos_agent_mcp.py`).
It has five tools:

- `list_clients`
- `spawn(task, client, card | tier, isolate, lean, …)`: returns a run id at
  once. Refusals come back synchronously from the CLI's own dry run.
  `lean` defaults on for `role=review`.
- `status(run_id?)`
- `result(run_id)`: the output tail.
- `cancel(run_id)`

Each run keeps `job.json`, `output.log` and `exit.json` under
`<repo>/logs/agents/<id>/` (git-ignored; `AUTOOS_STATE_DIR` overrides `<repo>/logs`, isolated clones go to `logs/sandboxes/`). The installers register the server,
using the pin in `catalog/agent-harness.json`, in these places:

- `.mcp.json` (the project server, approved in `.claude/settings.local.json`)
- the `mcp` servers of `opencode.jsonc`
- OpenHands `mcp_config`
- Zed `context_servers`

Only spawning roles list the server (orchestrator, suborchestrator).
`agent_harness.py check` refuses a leaf role that does, and leaf agents get
`autoos-agent*` switched off.

## Stuck-agent watchdog (tier1 probes, never waits forever)

Tier1 owns liveness. Every background tier2 gets a heartbeat line in
`logs/orch-<date>.log`; tier1 re-checks on a fixed interval and treats
silence as stuck — with one exception for rate limits (below):

1. **Probe every 5 minutes**: tier1 checks each live tier2 for a fresh
   heartbeat or report line. No new output in 15 minutes = stuck.
2. **Rate-limit ≠ stuck**: a `429 / quota / budget` message with a
   retry timestamp resets the clock — the agent is waiting, not dead.
   Only silence with no rate-limit trace counts as stuck.
3. **Kill + re-spawn once per track**: tier1 terminates the stuck
   tier2 and respawns it with the same track + DONE criteria (max 3
   respawns per track, then the track is marked blocked in
   `docs/tasks.md`).
4. **Never wait forever**: tier1 always sets a wall-clock deadline per
   track; on expiry it reconciles whatever reported and records the
   rest as open in `docs/handoff.md`.
5. **Log it**: every probe, kill, and respawn appends one line to
   `logs/orch-<date>.log` so the next tier1 can audit what happened.

## The tiers (ids are a contract — never rename, only relabel)

| Tier | Role | Context | Chain head (free-first) |
|---|---|---|---|
| `tier1` | orchestrator: plans, slices, verifies, never codes directly | 1M only, spark-only + `#high` (xhigh effort) | zen spark-contributor-free → openrouter spark-contributor → zen spark (paid). No gemini-3.1-pro (reasons worse than 3.8-flash). |
| `tier1-clean` | same, no prompt-training legs (sensitive data) | 1M, paid legs only | openrouter plain spark → zen paid spark. No free legs (any big free model may train). |
| `tier2` | smart worker: reasoning, reviews, mid-size codegen | ≤128k | gemini-3.8-flash → groq/cerebras/sambanova gpt-oss-120b → cheap-inference deepseek/glm/kimi → openrouter/deepseek flash |
| `tier2-clean` | same, paid legs only (sensitive data) | ≤128k | deepseek direct → openrouter/zen flash → mistral-small direct. No free legs, no reseller legs. |
| `tier3` | cheap driver: small edits, probes, parallel reviews | ≤128k | mistral-code → groq/cerebras qwen3.8-27b → cheap-inference glm-4.5-air/minimax-m2.7 → mistral-small → deepseek/zen flash |
| `tier3-clean` | same, paid legs only (sensitive data) | ≤128k | deepseek direct → mistral-small direct → zen paid flash. No free legs. |

Rule: anything under 1M context belongs in tier2/3, never tier1
(`opencode.jsonc` limits enforce this: 1M vs 128k). Small-context models
CAN sub-orchestrate — slice their tasks small instead of promoting them.

## The 3-tier run pattern

```
tier1 (1 orchestrator)
  plans the goal, slices into tracks, defines DONE per track
  ├─ tier2-A (sub-orchestrator, background): owns tracks 1..n
  │    ├─ tier3-a1 (reviewer, lens X, e.g. mistral/qwen leg)
  │    └─ tier3-a2 (reviewer, lens Y, e.g. deepseek leg)
  └─ tier2-B (sub-orchestrator, background): owns tracks n+1..m
       ├─ tier3-b1 (reviewer, lens X)
       └─ tier3-b2 (reviewer, lens Y)
```

Rules:

1. **Max fan-out 2+2+2** (1 tier1 → ≤2 tier2 → ≤2 tier3 each). Prevents
   quota collapse and keeps reviews reconcilable.
2. **Different lenses per tier3 pair**: one cheap-codegen leg
   (`tier3`/`tier3-clean`, mistral/qwen) + one smart leg
   (`tier2`/`tier2-clean`, gpt-oss/deepseek). Different models find
   different bugs; identical reviewers are wasted tokens.
3. **Rate-limit hygiene**: probes are `Reply with exactly: ack` +
   ONE focused review question each, small output budget. Reasoning
   models (spark) need a real budget (≥2048 tokens) or they return
   empty — that is a budget fault, not a routing fault.
4. **Reviews run in parallel**, reconciled by the owning tier2 before
   reporting up. Tier1 never gets two conflicting reviews directly.
5. **Verify, don't trust**: every tier2 runs its own tests
   (`tests/run-tests.ps1`, `tests/run-tests.sh`) and one live `ack`
   probe per touched tier before reporting.
6. **Non-overlapping files**: tier1 assigns tracks with disjoint file
   sets (e.g. A = configuration/remote/watchdog, B = catalog/combos/UI)
   so parallel workers never merge-conflict.

## Long-horizon continuation (the main-agent handoff)

A tier1 orchestrator that finishes its big part does NOT go idle:

1. Write the goal + plan + DONE-state to `docs/handoff.md`
   (what is proven, what is open, key commands, known issues).
2. Spawn (or ask the user to spawn) a NEW tier1 with the handoff as
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
| OpenHands | `http://<tail-ip>:3000` | PRIMARY agent UI (tier1 inside) |
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

- **opencode runtime** (this repo's harness): tier1/tier2/tier3 agents
  above, spawned via the `subagent` tool with `omniroute/tierN` models.
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

- Default model is `omniroute/tier1` (free legs first, paid overflow
  only from sanctioned providers). Paid Zen legs 402 without balance —
  the chain hops, by design.
- `auto/*` combos are the zero-setup bootstrap; strict `tierN`
  combos are the deterministic path for unattended runs.
- Sensitive data → `-clean` twins, always. When in doubt, `-clean`.

## Pipeline improvements (2026-09-20)

Proven this session by a 1→2→4 run (tier1 → tier2-A/B → dual tier3
reviewers each): disjoint file sets per track (A = watchdog/resilience,
B = catalog/UI/pipeline), parallel dual-lens reviews reconciled by the
owning tier2, one live `ack` probe per touched tier.

- **Disjoint file sets**: tier1 assigns non-overlapping tracks so parallel
  workers never merge-conflict.
- **Parallel dual-lens reviews**: one cheap-codegen leg
  (`tier3`/`tier3-clean`, mistral/qwen) + one smart leg
  (`tier2`/`tier2-clean`, gpt-oss/deepseek). Reconciled by the owning
  tier2; tier1 never sees two conflicting reviews directly.
- **Ack-probe budget**: reasoning models (spark) need ≥2048 output
  tokens or they return empty — a budget fault, not a routing fault.
  Keep probes at `Reply with exactly: ack` + one focused question.
- **Handoff rotation**: a tier1 that finishes its big part writes goal +
  plan + DONE-state to `docs/handoff.md` and a NEW tier1 re-plans only
  the OPEN items. Resumable across sessions, reboots, machines.
- **Watchdog auto-resume + human fallback ladder**: `healthcheck.*`
  probes :20128/:3000/:4096/:8777 log-only; `--fix`/`-Fix` resumes via
  `Start-AutoOSStack`. Ladder: OpenHands → opencode serve → Zed →
  Desktop → nvim+sidekick, all through :20128 with one client key.
- **Next gaps**: `--fix` should also resume `opencode serve :4096`
  when down (currently only gateway + container); the web Router-tiers
  card is display-only until backend apply/switch endpoints exist;
  `docs/README.md` still lacks the unattended-orchestration row.
