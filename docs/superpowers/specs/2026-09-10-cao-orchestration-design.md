# CAO Orchestration Lane — Design

**Date:** 2026-09-10
**Status:** Draft for review
**Scope:** `skills/unattended-orchestration/`, `infra/mcp-servers/cao-setup/`, `.mcp.json`, `README.md`, `AGENTS.md`

---

## 1. Summary

`unattended-orchestration` gains a second lane. The existing **batch lane**
(`run_handoff_sessions.ps1`) keeps owning headless overnight waves: worktrees, guards,
auto-merge, crash recovery. The new **CAO lane** owns interactive, hierarchical,
multi-provider orchestration with live inspection through the CAO server on `:9889`.

The skill's job is to give an orchestrating agent enough context and enough *mechanical*
rules that it plans before it spawns, spends the cheapest model that can do each job,
survives a provider quota wall, and never lets two agents work the same task.

Seven defects measured in the current CAO integration are fixed here (§9).

### What is genuinely new

| Capability | Mechanism |
|---|---|
| Planning happens once, at the top, interactively | `PLAN.lock.json` contract + a validator that refuses to dispatch without it (§3) |
| Configurable N-level hierarchy | Generated profiles whose tool surface *is* the depth limit (§4) |
| Claude, Gemini **and DeepSeek** as spawnable providers | Pool registry + per-task candidate ladders (§5.1, §5.5) |
| Model **and effort** chosen per task, not per level | `taskClass` → ordered `{pool, family, model, effort}` ladder (§5.3) |
| Quota walls survived without wasting any pool | Pool-keyed budget ledger + ladder descent + lease-based resume (§6) |
| Cheap inter-agent messages | Fixed envelopes with hard caps (§7) |
| Cross-**family** review | Dispatcher refuses same-family review pairs (§8) |
| Always-current models, no wasted quota | Ordered candidate ladders over quota pools (§5.3–5.4) |
| Runs on a Linux server | Python implementation and Python installer; no `pwsh`, no WSL (§2) |

---

## 2. Platform and language

**CAO does not require WSL.** Measured: CAO core contains zero `wsl` references outside
our own patch; its backends are `tmux_backend` and `herdr_backend`, both POSIX. WSL is a
*Windows* requirement only, because Windows has no tmux or POSIX FIFOs. On Linux CAO runs
natively and the WSL-to-Windows MCP bridge disables itself — its guard is
`resolved_str.startswith(("/mnt/", "/c/"))`, which never matches `/home/you/.gemini/...`.

**The CAO lane is implemented in Python 3.12**, not PowerShell.

Rationale, measured rather than assumed: `which pwsh` in the Ubuntu WSL distro exits 1.
A Linux server is a first-class target (req #2), and requiring an extra PowerShell install
there is a barrier the batch lane can afford but this lane should not. CAO itself
guarantees Python 3.12, so a Python implementation has a dependency that is *already
satisfied wherever CAO can run at all*. The skill already ships `trust_worktree.py`, so
Python is not a new language in this directory.

The batch lane stays PowerShell. The two lanes share **file formats**, not code:

| Shared | Not shared |
|---|---|
| NDJSON ledger conventions (append-only, one JSON object per line, redaction rules) | Ledger implementation — PowerShell reader, Python writer |
| `handoff.config.json` as the single per-repo config file | Runner entry points |
| `OMNIGRAPH_GRAPH_ID` = repo folder name (`trust_worktree.py:189`) | — |

A conformance test asserts both lanes' ledger lines satisfy one documented schema.

### New module layout

```
skills/unattended-orchestration/
  cao/
    __init__.py
    config.py        # load + validate the `cao` block
    plan.py          # PLAN.lock.json contract + validator
    profiles.py      # generate per-level CAO profiles from config
    routing.py       # taskClass -> ordered candidate ladder; pool/family split
    budget.py        # pool-keyed quota ledger + cooldown
    leases.py        # task ownership; safe auto-resume
    wire.py          # TASK / RESULT envelopes with hard caps
    dispatch.py      # the entry point everything else goes through
    cli.py           # `python -m cao <verb>`, run from the skill directory
  tests/cao/         # pytest
```

### Where agents run, and why — the placement rule

A fair challenge to an earlier draft: *if CAO does not need WSL, why must `claude` or `agy` be
installed inside it?* The premise is correct and the earlier answer was too quick. Measured
properly:

**Agents are children of CAO's tmux panes, so they execute wherever CAO executes.** On Linux
that is the host and none of this applies. On Windows, CAO needs WSL (tmux, POSIX FIFOs), so
its panes — and therefore its agents — live in WSL.

Could CAO-in-WSL spawn the *Windows* `claude.exe` through interop instead? **Measured: yes, it
executes** — `/mnt/c/.../claude.exe --version` returns `2.1.267`, exit 0, WSLInterop enabled.
So this is a live option, not a theoretical one. Three things break it:

1. **Working directory.** CAO forwards Linux paths (`--cwd /home/...`). A Windows binary needs
   `C:\...`. Untranslated — the same boundary that caused the original `%PATH%` failure.
2. **Split config roots.** WSL `claude` reads `/home/<you>/.claude`; `claude.exe` reads
   `C:\Users\<you>\.claude`. Different MCP config, different auth, different settings.
3. **TUI driving.** CAO drives Claude Code via `tmux send-keys` plus ANSI-regex parsing tuned
   to a Linux PTY (`claude_code.py:103-111`). A Windows console app in that PTY is untested.

**But the binary was never the expensive part — the filesystem is.** Measured on this host:

| Layout | `git status --porcelain` | Relative |
|---|---|---|
| Repo on `/mnt/c` (9p), agent in WSL | **3.021 s** | 1× |
| ext4 worktree, `.git` still on `/mnt/c` | **0.146 s** | **20× faster** |
| Fully ext4 | **0.017 s** | 178× faster |

An agent runs `git status`, file reads and test discovery constantly. At 3 s per status call, a
long-horizon run spends more time on 9p than on thinking.

**The placement rule, stated once:**

> Install agents in the same OS environment as CAO — WSL on Windows, native on Linux — and put
> CAO **worktrees on ext4**, not on `/mnt/c`.

The main checkout stays on Windows so the IDE and Windows tooling keep working; only the
worktrees CAO provisions (`assign(use_worktree=True)`) move to ext4. A 20× measured win for one
config field, costing nothing on a Linux server where the rule collapses to "everything is
native anyway".

`cao.worktreeRoot` (default `$HOME/cao-worktrees`) carries this, and a preflight check warns
when the resolved worktree root sits under `/mnt/`.

### Installation and setup in Python (req #3, new)

`setup-cao.sh` is replaced by **`setup_cao.py`**, keeping a ~10-line `setup-cao.sh` shim that
only locates `python3` and re-execs — so muscle memory still works and a missing interpreter
produces a clear message rather than a syntax error.

Honest evaluation, since the question was whether this makes things easier *or harder*:

| | Bash | Python |
|---|---|---|
| JSON edits (`mcp_config.json`, `settings.json`) | needs `jq`; no atomic write | stdlib, atomic temp+rename |
| YAML profiles | needs `yq` | already a dependency of the lane |
| Idempotent patching of `antigravity_cli.py` | `sed`/`grep` heuristics, easy to double-apply | real parse + marker check |
| Cross-platform driver | Windows needs git-bash to drive WSL | one script on Windows/Linux/macOS |
| Testable | ad-hoc | `pytest`, alongside the rest of the lane |
| Bootstrap dependency | POSIX shell | `python3` |

The only genuine cost is the bootstrap dependency, and it is not a real one: **CAO itself
requires Python 3.12**, so any host that can run CAO can run the installer. WSL Ubuntu has
3.12.3; every target Linux ships python3.

What this replaces or absorbs:

| Present file | Becomes |
|---|---|
| `cao-setup/setup-cao.sh` | thin shim → `setup_cao.py` |
| `cao-setup/patches/apply_wsl_bridge_patch.sh` | absorbed into `setup_cao.py` as an idempotent, marker-guarded patch step |
| `cao-setup/profiles/*.yaml` (hand-written) | generated by `cao/profiles.py` from config (§4) |
| `cao-setup/workflows/three_layer_orchestration.yaml` | generated N-layer workflow (§4) |

Hand-written profiles and the fixed three-layer workflow stop being the source of truth —
config is. They stay in git as *generated output*, so a diff reveals when regeneration is
stale.

---

## 3. The planning gate (req #3)

### The problem

Instructions alone do not hold. The current workflow starts executing the moment a task is
submitted, and the existing profiles tell agents to plan without any mechanism that checks
they did. Prompt text is not a gate.

### The mechanism

The gate is an **artifact**. `PLAN.lock.json` lives at
`docs/superpowers/plans/<YYYY-MM-DD>-<topic>/PLAN.lock.json` — committed and reviewable
alongside the other plans, because a contract that vanishes with a gitignored state
directory cannot be audited after the fact, and it must survive worktree teardown.

```json
{
  "schema": "cao-plan/1",
  "goal": "one sentence",
  "createdBy": "claude-code",
  "approvedAt": "2026-09-10T14:22:03Z",
  "depth": 3,
  "openQuestions": [
    { "q": "Which graph should workers write to?",
      "answer": "agent-skills",
      "answeredBy": "user" }
  ],
  "assumedDefaults": [],
  "phases": [
    { "id": "p1",
      "goal": "...",
      "taskClass": "implement",
      "acceptance": { "guard": "pytest tests/cao -q", "expect": "exit0" },
      "dependsOn": [] }
  ],
  "refusals": [],
  "notForAgents": []
}
```

`plan.validate()` runs **before any session launches**, from `dispatch.py`. It throws unless:

1. The file exists and matches schema `cao-plan/1`.
2. `approvedAt` is present and parses.
3. Every phase has a non-empty `acceptance.guard` — a phase with no way to be checked is
   not a phase.
4. `dependsOn` forms a DAG over existing phase ids.
5. `depth` is within `cao.maxDepth`.
6. **Either** at least one `openQuestions` entry has `answeredBy: "user"`, **or**
   `assumedDefaults` is non-empty and each entry records what was assumed and why.

Rule 6 is the interactive gate. An orchestrator that wants to skip asking must write down,
in a committed file, exactly what it decided on the user's behalf. Silence is not an option
the format allows.

### Who plans

| Layer | Plans? | Rule in its generated system prompt |
|---|---|---|
| 1 — Orchestrator | Yes — asks the user | "Ask before you spawn. Fill `openQuestions`." |
| 2 — Supervisor | No | "The plan is final. Decompose it; do not re-open it." |
| 3+ — Worker | No | "Execute your TASK. Report evidence. Do not re-plan." |

A sub-orchestrator that finds the plan wrong does **not** re-plan. It emits
`RESULT status=needs_revision` with the contradicting evidence and stops. Layer 1 decides.
This is the corrective channel you asked for: evidence flows up, authority stays at the top.

### Rejected alternative

A CAO two-phase workflow (`plan` step, then `execute` step). CAO workflow steps run *inside
agent terminals*, which would put planning in a subagent — the exact thing req #3 forbids.

---

## 4. Configurable depth (req #4)

### CAO has no depth limit

Measured: the only `depth` in CAO is `list_siblings` prefix clamping
(`terminal_service.py:1475`). Nothing bounds spawn nesting. Depth is emergent from whether a
profile's tool surface contains `assign` / `handoff`.

This is not a gap to complain about — it is the enforcement point. **A profile without
`assign` cannot spawn, because the tool is not in the terminal.** That matters especially for
Gemini: unlike `claude` and `grok`, the `agy` adapter has no `leafEnforcement` flag
(`AgentAdapters.psm1:26-41`), so there is no CLI switch to forbid delegation. Withholding
the tool is the only real control.

### Config

A new `cao` block in `handoff.config.json` — verified safe, the existing validator has no
unknown-key rejection:

```json
"cao": {
  "maxDepth": 3,
  "server": "http://localhost:9889",
  "worktreeRoot": "$HOME/cao-worktrees",
  "levels": [
    { "level": 1, "role": "orchestrator", "runner": "claude-code", "canSpawn": true },
    { "level": 2, "role": "supervisor",   "canSpawn": true  },
    { "level": 3, "role": "worker",       "canSpawn": false }
  ],
  "pools": {
    "anthropic":   { "via": "claude"   },
    "antigravity": { "via": "agy"      },
    "deepseek":    { "via": "opencode", "apiKeyEnv": "DEEPSEEK_API_KEY" }
  },
  "reviewPairing": "cross-family",
  "resume": { "enabled": true, "leaseMinutes": 30, "maxAttempts": 3 }
}
```

Levels carry no model. Model and effort come from `taskClass` (§5) so that a level's *depth
role* and a task's *difficulty* stay independent — a level-3 worker doing a hard
implementation should not be forced onto the same model as a level-3 worker doing a rename.

### Enforcement: generate, don't ask

`profiles.generate(config)` writes one profile YAML per level into `$CAO_HOME_DIR/profiles/`:

- `canSpawn: true` → `allowedTools` includes `assign`, `handoff`, `list_siblings`,
  `report_outcome`, `send_message`.
- `canSpawn: false` → `allowedTools` **omits** `assign` and `handoff`. Leaf by construction.

Setting `maxDepth: 4` regenerates a four-level set with no hand-written YAML. Setting
`maxDepth: 2` produces a supervisor-less two-level set.

Second belt: every dispatched TASK carries `depth: N/maxDepth`, and the generated system
prompt states the level's own position. Belt (tool surface) and braces (prompt) — the belt
is what actually holds.

A test asserts: for every `maxDepth` in 2..5, the deepest generated profile contains neither
`assign` nor `handoff`.

---

## 5. Providers, models, effort (reqs #1, #4.1, #8)

### 5.1 Dual provider

Both providers are first-class and spawnable, manually or by an orchestrator:

| Provider | CAO provider id | Binary | Notes |
|---|---|---|---|
| Claude Code | `claude_code` | `claude` | Effort via `claudeConfig.effort` → `--effort` |
| Antigravity / Gemini | `antigravity_cli` | `agy` | Effort encoded in the model id |

**Gap, now closed.** `claude` was installed on Windows (`~/.local/bin/claude`, 2.1.267) but
**not inside WSL**, which is why `GET /health` reported `"claude":"unavailable"`. CAO runs in
WSL and can only spawn what WSL can execute.

Resolved 2026-09-10: Claude Code 2.1.236 installed into the Ubuntu distro via Anthropic's
native installer (`https://claude.ai/install.sh`), **not** the `npm` on PATH — measured, that
`npm` resolves to `/mnt/c/Program Files/nodejs/npm`, the Windows one, which would install a
Windows package into a Linux prefix. `GET /health` now returns `"claude":"ok"`, picked up by
the already-running server with no restart.

#### Why herdr is not a substitute

herdr 0.8.0-preview is installed on Windows, and CAO ships a `herdr_backend`. It does **not**
remove the need for `claude` inside WSL:

1. **It is a terminal backend, not a cross-OS agent bridge.** `BackendFactory.create`
   (`factory.py:41-46`) selects who owns *panes*; CAO still executes `claude` or `agy` inside
   whichever pane the backend created, on that pane's machine. It changes window management,
   not binary reachability.
2. **It would reintroduce the original bug class.** `herdr_backend.py:298` forwards
   `--cwd <working_directory>` as a raw path. CAO-in-WSL produces `/home/...` or `/mnt/c/...`;
   a Windows `herdr.exe` requires `C:\...`. That untranslated boundary is precisely what
   produced the `%PATH%` failure this integration started from.
3. **It is opt-in and experimental** (`factory.py:4`). Adopting it would swap the working
   tmux backend for an untested one — a large blast radius for a narrow goal.

herdr therefore stays where the `herdr-orchestration` skill already puts it: a supervised,
human-present multiplexer, not part of this lane's spawn path.

#### Automatic detection and installation

`setup_cao.py` **detects and installs missing providers automatically**, so the same script
bootstraps a Windows/WSL workstation and a bare Linux server:

| Step | Behaviour |
|---|---|
| Detect | `command -v claude`, `command -v agy`, `command -v cao`; cross-check `GET /health` |
| Install `claude` | Anthropic native installer into `$HOME`; never `sudo`; never the PATH `npm` (may be Windows npm under WSL) |
| Install `agy` | Vendor installer; skipped with a clear message where unavailable |
| Verify | Re-probe `/health`; fail loudly if a provider named in `routing` is still missing |
| `--check` | Report only, install nothing — the CI/idempotence path |

Rules the script obeys: idempotent (safe to re-run), non-interactive (`--yes` for unattended
bootstrap), and **never silently degrades** — a missing provider that `routing` depends on is
an error, not an all-Gemini fallback nobody notices. A test asserts `/health` lists every
provider the config routes to.

### 5.2 Two orthogonal axes

An earlier draft of this spec conflated them and got the design wrong. They must stay
separate:

| Axis | What it means | What it decides |
|---|---|---|
| **Quota pool** | Which billing/limit bucket the capacity is drawn from | Fallback when a pool is exhausted (§6) |
| **Model family** | Whose training, whose blind spots | Cross-provider review pairing (§8) |

They are not the same thing. `claude-opus-4-6-thinking` reached through `agy` is the
**anthropic family** drawn from the **antigravity pool**. That distinction is what makes it
both a legitimate source of extra capacity *and* an invalid reviewer for Claude-written code.

| Pool | Reached via | Families it can serve |
|---|---|---|
| `anthropic` | `claude` CLI | anthropic (`opus`, `sonnet`, `haiku` — aliases) |
| `antigravity` | `agy` | google (`gemini-3.8-flash-*`, `gemini-3.1-pro-*`), anthropic (`claude-opus-4-6-thinking`, `claude-sonnet-4-6`), oss (`gpt-oss-120b-medium`) |
| `deepseek` | `opencode` | deepseek (`deepseek-v4-flash`, `deepseek-v4-pro`) |

### 5.3 Routing — ordered candidate ladders

Each task class maps to an **ordered list of candidates**, not a single choice. The dispatcher
takes the first candidate whose pool is not cooling (§6). This is where "smart models on hard
work, fast models on mechanical work" (req #4.1) is expressed once:

```json
"routing": {
  "plan":       [ {"pool":"anthropic","family":"anthropic","model":"opus","effort":"high"},
                  {"pool":"antigravity","family":"anthropic","model":"claude-opus-4-6-thinking"},
                  {"pool":"antigravity","family":"google","model":"gemini-3.1-pro-high"} ],
  "decompose":  [ {"pool":"anthropic","family":"anthropic","model":"sonnet","effort":"high"},
                  {"pool":"antigravity","family":"anthropic","model":"claude-sonnet-4-6"} ],
  "research":   [ {"pool":"antigravity","family":"google","model":"gemini-3.8-flash-high"},
                  {"pool":"deepseek","family":"deepseek","model":"deepseek-v4-flash"} ],
  "implement":  [ {"pool":"antigravity","family":"google","model":"gemini-3.8-flash-high"},
                  {"pool":"anthropic","family":"anthropic","model":"sonnet","effort":"medium"} ],
  "mechanical": [ {"pool":"deepseek","family":"deepseek","model":"deepseek-v4-flash"},
                  {"pool":"antigravity","family":"google","model":"gemini-3.8-flash-low"} ],
  "test":       [ {"pool":"antigravity","family":"google","model":"gemini-3.8-flash-medium"},
                  {"pool":"deepseek","family":"deepseek","model":"deepseek-v4-flash"} ],
  "verify":     [ {"pool":"antigravity","family":"google","model":"gemini-3.8-flash-medium"},
                  {"pool":"deepseek","family":"deepseek","model":"deepseek-v4-flash"} ],
  "review":     "cross-family"
}
```

Effort is a function of task class, not level — req #4.1's "effort should also be determined
by the task". For Gemini it rides in the model-id suffix (`-high` / `-medium` / `-low`),
because CAO's `_build_agy_command` passes `--model` but never `--effort`. For Claude it goes
into the generated profile's `claudeConfig.effort`, which CAO maps to `--effort <level>`
(`claude_code.py:332`).

### 5.4 Model freshness — corrected (req #8)

The earlier draft proposed a **deny list** that would have rejected `claude-opus-4-6-thinking`
outright. That was wrong, and the correction matters:

> **Never decline capacity.** `agy`'s Claude 4.6 models draw on the *antigravity* pool, which
> is separate quota from Claude Code's *anthropic* pool. Refusing them does not get you a
> better model — it gets you a smaller total budget.

Freshness is therefore a rule about **ordering within a pool**, not a ban:

1. **Prefer aliases where the CLI offers them.** `claude --model` documents that it accepts
   "an alias for the latest model", so `opus` / `sonnet` / `haiku` pick up a new release with
   no config change.
2. **Within a pool, rank candidates newest-best-first.** `routing.validate()` warns (not
   errors) when a pool lists an older model *ahead of* a newer one that same pool also offers
   — e.g. `gemini-3.6-flash-high` before `gemini-3.8-flash-high`.
3. **Across pools, order by capability, then let quota decide.** A ladder may legitimately
   descend to an older family once the better pool is exhausted. That descent is recorded in
   the RESULT and the DONE note, so a report never hides which model actually did the work.

A test asserts no default ladder places an older model ahead of a newer one from the same
pool — a stale default fails CI rather than shipping silently.

### 5.5 DeepSeek as a third provider (req #2, new)

**Verdict: possible, with zero CAO patching.** DeepSeek-V4.1-Flash (released ~2026-09-10;
552B MoE, 8B active for input / 16B for output, native multimodal, ~420 tok/s) is a strong
fit for the `mechanical` / `test` / `verify` tiers and gives a genuinely *third* reviewer
family.

Route, in order of preference:

| Route | Cost | Verdict |
|---|---|---|
| **`opencode_cli` provider** (CAO built-in) + DeepSeek API key | Install `opencode`; `opencode auth` / `/connect`; provider block in `opencode.json` | **Chosen.** No CAO patch |
| Custom CAO provider | `ProviderManager` uses **static imports** (`manager.py:9-23`), not entry points — a new provider means patching CAO again | Rejected: a second vendored patch to maintain |
| Via `agy` | `agy models` lists no DeepSeek | Not available |

Measured: CAO ships `providers/opencode_cli.py` and passes `--model` through
(`opencode_cli.py:188`). `opencode` is **not yet installed**; `setup_cao.py` installs it when
`routing` references the `deepseek` pool.

The DeepSeek API key is a **secret**. It is read from the existing `envFrom` mechanism, never
written into a committed config, and never handled in plaintext by an agent. Until a key is
present, the `deepseek` pool is marked unavailable and ladders skip it — the same mechanism as
a quota-cooling pool, so an absent key degrades gracefully instead of erroring.

---

## 6. Quota and safe resume (req #5)

### 6.1 Provider-agnostic

The budget ledger is keyed by **quota pool** (§5.2), not by provider and certainly not
hardcoded to Gemini. Pool is the right key because it is what actually runs out:

```json
{ "pool": "antigravity", "state": "cooling",
  "cooling_until": "2026-09-10T18:00:00Z", "detected_at": "...",
  "pattern": "<the matched pattern>", "raw": "<first 500 chars>",
  "observed": { "in": 412000, "out": 38000, "requests": 214 } }
```

`budget.check(pool)` runs before every dispatch. Cooling → the dispatcher walks to the next
candidate in that task class's ladder (§5.3) and records the descent in the phase's DONE note,
so a report never silently misrepresents which model did the work.

A pool with no credentials (e.g. `deepseek` before an API key exists) is marked
`state: "unavailable"` and skipped by the same code path — an absent provider degrades exactly
like an exhausted one, rather than raising.

**Honest limitation.** I do not know either provider's exact quota-error string, and I will
not hardcode a guess. Detection patterns are config-driven with a conservative default set
(`rate.?limit`, `quota`, `resource.?exhausted`, `429`, `usage limit`). If a real limit is
provoked during testing, the measured string is pinned and the spec updated. Until then the
patterns are documented as **unverified**, and `budget.check` fails *open* (allows dispatch)
rather than falsely cooling a healthy provider.

### 6.2 Cost awareness

Every `RESULT` envelope carries `cost: {provider, model, in, out}`. `dispatch.py` accumulates
per-phase totals into the ledger. Layer 1 can compare a phase's spend against
`phases[].budgetTokens` and downgrade the routing tier for the remainder — the mechanism
being simply that the table is consulted per task, not per run.

### 6.3 Auto-resume — and the lease that makes it safe

Your constraint: resume stopped work, **but only if no other agent already continued it.**

Each task has one row in `leases.ndjson`:

```json
{ "task_id": "p2-impl-03", "owner": "term-a91f", "state": "running",
  "lease_expires": "2026-09-10T15:05:00Z", "continued_by": null, "attempt": 1 }
```

Claiming is atomic — `os.open(path, O_CREAT | O_EXCL)` on a per-task lock file, which is
atomic on POSIX and on NTFS. The resume sweep may restart a task **only** when all hold:

1. `state` is `running` or `quota_stalled`, and
2. `lease_expires` is in the past, and
3. `continued_by` is null, and
4. no live CAO terminal reports that `task_id` — checked against
   `GET /sessions` on `:9889`, not against the ledger alone.

Condition 4 is the one that matters. A ledger can be stale; the server knows who is actually
alive. Reusing the batch lane's existing orphan-detection idea (`Reconcile-HandoffLedger`),
but reading liveness from CAO rather than from process handles.

On a successful claim, `attempt` increments and the previous owner is recorded. A task that
exceeds `maxAttempts` (default 3) goes to `state: needs_human` and stops — a resume loop
that never converges is worse than a stall.

---

## 7. Inter-agent wire protocol (req #6)

With three or more layers, prose between agents is the dominant cost and the dominant source
of drift. Messages are **fixed envelopes with hard caps**, emitted by `wire.py`.

**TASK** (cap 800 tokens):

```
TASK p2-impl-03
goal:       <=200 chars
plan_ref:   PLAN.lock.json#/phases/p2
files:      src/a.py, src/b.py
acceptance: pytest tests/test_b.py -q -> exit0
depth:      3/3
budget:     40000 tokens
reply:      RESULT -> term-a91f
```

**RESULT** (cap 400 tokens):

```
RESULT p2-impl-03 status=ok|blocked|needs_revision
evidence:      pytest tests/test_b.py -q -> 14 passed
files_changed: src/b.py
notes:         <=300 chars
cost:          gemini/gemini-3.8-flash-high in=18211 out=2044
```

Three rules, enforced in code rather than requested in prose:

1. **Never re-quote the plan.** Reference `plan_ref`. `wire.render_task()` has no field that
   can carry plan prose.
2. **Never paste file content over 20 lines.** Reference `path:line`. Longer content is
   truncated with an explicit marker.
3. **`notes` is capped.** Truncation is visible (`…[truncated N chars]`), never silent.

A test asserts a 50 KB input renders to an envelope under the cap with a visible truncation
marker.

---

## 8. Cross-provider review (req #7)

The rule: **the reviewer's model family must differ from the implementer's.** Different
training, different blind spots — a same-family review shares the failure mode it is meant to
catch.

**Family, not pool** (§5.2). Reviewing Claude-written code with `claude-opus-4-6-thinking`
*via agy* would spend a different quota bucket but consult the same blind spots. It is a valid
fallback for capacity and an invalid one for review. The dispatcher pairs on `family`.

`dispatch.review(task)` picks the highest-ranked candidate whose `family` differs from the
implementer's, skipping cooling pools:

| Implemented by (family) | Reviewed by (family) | Typical candidate |
|---|---|---|
| anthropic | google, then deepseek | `gemini-3.8-flash-high` |
| google | anthropic, then deepseek | `sonnet` @ effort high |
| deepseek | anthropic, then google | `sonnet` @ effort high |

Three families means a same-family review is now genuinely rare — DeepSeek gives the pairing a
second escape hatch before it has to degrade.

If no differing family is reachable, the dispatcher **throws** rather than quietly degrading.
Exception: when every other family is quota-cooling (§6), the review proceeds same-family and
is tagged `review_degraded: true` in the RESULT and the DONE note — degraded and *labelled*,
never degraded and hidden.

---

## 9. Defects fixed

| # | Defect | Fix | Test |
|---|---|---|---|
| 1 | Every CAO agent writes to Omnigraph graph `memory`, not the repo graph — the trap documented in `CLAUDE.md` from 2026-07-17 | Extend the same `_register_mcp_servers` hook that injects `cao-mcp-server` to also inject a per-terminal `omnigraph` entry, graph id derived as `trust_worktree.py:189` does (repo folder name of the terminal's cwd) | Launch a terminal; assert its injected entry carries the repo graph id, and that teardown removes it |
| 2 | Claude cannot drive CAO from this repo; `claude` unavailable to CAO | Add `cao-ops` to `.mcp.json` + `enabledMcpjsonServers`; document `wsl -d Ubuntu bash -lc 'cao …'` as the always-works fallback; **`claude` installed into WSL — done, `/health` now `"claude":"ok"`** (§5.1) | `/health` lists every routed provider |
| 3 | No depth control in CAO | Generated leaf profiles (§4) | For `maxDepth` 2..5, deepest profile has no `assign`/`handoff` |
| 4 | The bridge patch is a 943-line vendored copy; `cao update` silently reverts it | Test asserts the bridge is present in the **live** `antigravity_cli.py`; `setup_cao.py` applies it idempotently behind a marker check | Bridge-presence test fails loudly post-update |
| 5 | `agy` resolves under `bash -lc` but not `bash -c` — the original `%PATH%` bug class | Every wrapper uses `bash -lc` or an explicit PATH export | Test greps all generated wrappers for one of the two |
| 6 | Bridge is Windows-shaped; unproven on Linux | Bridge is already conditional on `/mnt/`, `/c/` | Test asserts it is a **no-op** for a `/home/...` config path |
| 7 | Agents run against `/mnt/c` (9p): `git status` costs **3.021 s** vs 0.017 s on ext4 — a 178× tax paid on every file operation of a long-horizon run | `cao.worktreeRoot` defaults to `$HOME/cao-worktrees` (ext4); measured 0.146 s, a 20× win with `.git` still on `/mnt/c` (§2) | Preflight warns when the resolved worktree root is under `/mnt/`; benchmark recorded in the E2E transcript |

---

## 10. Verification plan

**Unit (pytest, `tests/cao/`)** — plan validator accept/reject; profile generation across
depths 2..5; deny-list rejection of stale model ids; envelope caps and truncation markers;
budget cooldown and fail-open; lease claim atomicity under concurrent claimers; DAG
validation.

**Integration (against the live server on `:9889`)** — profile install; terminal launch;
per-terminal `omnigraph` injection and teardown; bridge no-op on a Linux-shaped path.

**End-to-end — the evidence that matters.** One real round trip:
Layer 1 (Claude, this session) → Layer 2 supervisor → Layer 3 worker `assign` → `RESULT`
back up → cross-family review → phase report. Transcript captured to
`docs/superpowers/plans/`.

This is the run the previous walkthrough never performed. Its verification confirmed a
supervisor *booted* with tools loaded and inferred that delegation worked; it never observed
an `assign` reaching a worker or a result coming back. Booting is not delegating, and the
report will not claim a round trip until one is observed.

---

## 11. Documentation (req #9)

| File | Change |
|---|---|
| `README.md` | New orchestration section: two lanes, when each applies, the `:9889` dashboard, provider matrix, pointer to the skill |
| `AGENTS.md` | Router row updated: `unattended-orchestration` covers batch **and** CAO multi-provider hierarchy |
| `skills/unattended-orchestration/SKILL.md` | §9 rewritten around this design; the planning gate stated as a refusal rule, not advice |
| `skills/repository-index/SKILL.md` | `cao-ops` entry; Python module layout under §5 |
| `infra/mcp-servers/cao-setup/README.md` | New: Windows/WSL **and** Linux install paths, `--check`, provider availability, DeepSeek key setup |
| `README.md` (orchestration section) | The placement rule and the ext4-worktree measurement — the single highest-leverage config choice on a Windows host |

---

## 12. Decisions taken (2026-09-10)

| Question | Decision | Consequence |
|---|---|---|
| Install `claude` into WSL? | **Yes — done.** 2.1.236 via Anthropic's native installer; `/health` now `"claude":"ok"` | Genuinely multi-provider hierarchy; cross-family review works at every layer |
| Does herdr make that unnecessary? | **No** — it is a pane backend, forwards untranslated `--cwd`, and is experimental (§5.1) | herdr stays out of this lane's spawn path |
| Where does `PLAN.lock.json` live? | `docs/superpowers/plans/<date>-<topic>/`, committed | Contract is auditable after the run and survives worktree teardown |
| Resume `maxAttempts`? | **3**, then `state: needs_human` | Survives two transient quota walls; a non-converging loop cannot burn the night |
| Provider install | **Automatic** detection + install in `setup_cao.py`, with `--check` for report-only (§5.1) | One script bootstraps Windows/WSL and a bare Linux server |

### Second review round (2026-09-10)

| Question | Decision |
|---|---|
| Use `agy`'s Claude 4.6 models? | **Yes.** Separate quota pool — refusing it shrinks the budget without improving the model. The deny list was wrong and is replaced by ordered ladders (§5.4) |
| DeepSeek as a third provider? | **Yes, via `opencode_cli`** — zero CAO patch. `deepseek-v4-flash` added to the `mechanical`/`test`/`verify`/`research` ladders (§5.5). Needs an API key; absent key = pool `unavailable`, ladders skip it |
| Setup in Python? | **Yes.** `setup_cao.py` replaces `setup-cao.sh`; the bridge patch script is absorbed; hand-written profiles/workflows become generated output (§2) |
| Why WSL for agents if CAO doesn't need it? | Premise correct. Agents are children of CAO's panes, so they live where CAO lives. Windows `claude.exe` *does* run under interop (measured), but breaks on `--cwd` translation, split config roots, and PTY-tuned TUI parsing. **The real cost is the filesystem**: 3.021 s vs 0.146 s for `git status`. Hence the placement rule + ext4 worktrees (§2) |

### Still open

- **Quota-detection strings are unverified** (§6.1). They stay marked as such until a real
  limit is provoked and the actual message measured. `budget.check` fails open until then.
- **DeepSeek API key not yet present.** The `deepseek` pool stays `unavailable` until one is
  supplied through `envFrom`. Secrets are never committed and never handled in plaintext.

---

## 13. Non-goals

- Replacing the batch lane. It stays the headless-overnight tool.
- Forking CAO. The patch stays minimal and re-appliable; upstream owns the rest.
- Claiming quota-string detection is verified before it is measured (§6.1).
