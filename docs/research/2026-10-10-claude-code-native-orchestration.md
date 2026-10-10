# Claude Code native orchestration vs. what AutoOS built

Date: 2026-10-10. Host CLI: Claude Code **2.1.296** (`claude --version`). Status: **research only — nothing
was changed**. The operator decides each adoption separately (brief: routing D-952, rules D-914/D-935/D-942).

Question: much of the orchestration and messaging AutoOS hand-built is now native in Claude Code. Which
native features should we adopt to save time, tokens and money, what does that let us delete, and what
must stay ours?

## TL;DR

1. **Native features replace the coordination layer *between Claude sessions*.** That layer covers
   L0/L1/L2 messaging, waking, watching, heartbeats, post-compaction rule recall, notifications and
   permission set-up. Most of what we built there now has a native, documented equivalent.
2. **They do not replace the multi-vendor L3 spawner.** `tools/autoos-agent.py` (13,146 lines),
   `autoos_resolver.py` and `oc_l2.py` exist because L3 work runs on non-Claude legs under the
   Claude-budget policy. Native subagents and workflows run Claude models on the Claude plan, so they
   cannot replace those legs. One bridge exists: the Claude Code harness pointed at the gateway's
   `/v1/messages` endpoint. OS-30 measured it working on one free leg.
3. **The biggest cheap wins** need configuration only, no code:
   - set `crossSessionInbound` for our bypass-mode orchestrators, so peer messages stop being held and
     dropped;
   - add a `SessionStart` hook with matcher `compact`, so the rules survive compaction. The skill is
     26 KB, over the 5,000-token re-inject cap, so its tail is lost today;
   - wait with `notify_when_idle` and `Monitor` instead of empty cron beats;
   - set `autoMode.environment` in user scope, so remote-host operations stop being classified as
     leaving the trust boundary.
4. **Little code can be deleted outright.** Roughly 250–400 lines of runner PowerShell and some skill
   prose become deletable only after each migration is measured. The real saving is incidents and
   tokens, not lines.

## Method and sources

- **Official docs:** read directly from `code.claude.com/docs/en/*.md` on 2026-10-10, current for CLI
  2.1.296. Pages: `cross-session-messaging`, `agent-view`, `scheduled-tasks`, `auto-mode-config`,
  `mcp`, `sub-agents`, `workflows`, `context-window`, plus `permission-modes`, `hooks`,
  `prompt-caching`, `fast-mode`, `costs`, `model-config` and `tools-reference` via the docs leg.
  Version numbers below are the ones each page states.
- **Two sources per claim:** each key claim was read by the `claude-code-guide` docs leg and then
  re-read verbatim by this session. A different-family leg cross-checked the claims and the
  file:line anchors (see `.l2/REPORT.md`, not committed).
- **Unverified claims** are marked **UNVERIFIED**.
- **Our side:** read at main `52fbd792`. Every anchor is `path:line` at that commit.

---

## 1. Native mechanisms, by topic

### 1.1 Agent-to-agent communication

| Fact | Source |
|---|---|
| `SendMessage` + `ListAgents` reach subagents, team-mates and *other local sessions*. On by default from v2.1.224 (macOS/Linux) and v2.1.234 (Windows). | cross-session-messaging §Availability |
| Same-machine transport is a per-session Unix socket (`CLAUDE_CODE_MESSAGING_SOCKET`, token `CLAUDE_CODE_MESSAGING_TOKEN`) and never passes through Anthropic servers. Other machines and cloud sessions go via Remote Control. | §Message sessions on other machines, §The session's inbox socket |
| Delivery happens between tool calls; an idle receiver starts a new turn. Each arrival is **Delivered / Held / Refused**. | §Message delivery |
| **No read receipt.** The sender sees `Not sent` only for refusals made before sending: size above ~1M chars, a burst limit, or a refusing remote. | §Message delivery, §Limitations |
| **Default inbound rule depends on permission class.** A receiver that *bypasses* permissions **holds** every message unless the sender also bypasses. A prompting receiver holds only messages from bypassing senders. | §Control inbound messages |
| A held message in a `-p` or IDE session is dropped after `dialogExpiry` (default **5 min**). A background session with no terminal attached keeps the dialog open until someone attaches. The hold queue keeps 100 and drops the oldest. Receiver queue: at most 50 accepted messages. | §Control inbound messages, §Non-interactive sessions, §Limitations |
| `crossSessionInbound: accept\|hold\|refuse` overrides the default. Docs: "To let a `-p` worker take messages unattended, start it with `crossSessionInbound` set to `accept` in its `--settings` value." | §Non-interactive sessions |
| `notify_when_idle`: one notice when a local session next goes idle or exits. Costs the watched session nothing; expires after 12 h; main conversation only. | §Get a notice when another session goes idle |
| Name collisions: a later session with the same name gets a variant; when names still collide, addressing needs the short `[ref]`. We hit this: 7 sessions named `autoos-L1-main`, today. | §See which sessions Claude can reach |
| Background sessions: since v2.1.208, a reply that can't be delivered is saved and sent as the next prompt when the process restarts. Since v2.1.260, messages to a backgrounded conversation follow it. | agent-view changelog v2.1.208, v2.1.260 |
| Restart, `/clear` or compaction with a message queued in a live, non-background session: **UNVERIFIED**, not documented. Treat as lost. | — |

### 1.2 Subagents vs. agent teams vs. background sessions

| Mechanism | Fits | Limits (docs) |
|---|---|---|
| **Subagent** (`Agent` tool, `.claude/agents/*.md`) | A focused task where only the result should come back. Context isolation. | Nests up to **3 layers** by default (v2.1.219+; `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`). **20 concurrent** (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, v2.1.217+). Resume by `SendMessage` to the agent id or name. `isolation: worktree`. Frontmatter `model`/`effort`/`tools`/`permissionMode`. A parent in bypass, acceptEdits or auto *overrides* the child's `permissionMode`. (sub-agents) |
| **Agent team** | Peers that must debate or share a task list | Experimental, off by default (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Interactive only, not `-p` or SDK. One team per session; team-mates can't spawn team-mates. ~7× tokens in plan mode. (agent-teams, costs) |
| **Background session** (`claude --bg`, `claude agents [--json]`, `attach`/`logs`/`stop`/`respawn`/`rm`) | Unattended work the operator can join | Research preview. A supervisor runs each session as its own process. A machine shutdown stops sessions. An unattached finished or waiting session's process stops after ~1 h and resumes on reply (`Ctrl+T` pins it). Dispatched sessions get their own worktree (`worktree.bgIsolation`), and never push to main, force-push or merge. `--json` gives `state` (working/blocked/done/failed/stopped), `status` and `waitingFor` (`permission prompt`, `input needed`, …). (agent-view) |
| **Worktree** (`--worktree`, `EnterWorktree`) | File isolation only, not orchestration | `.worktreeinclude` copies git-ignored files. (worktrees) |

### 1.3 Dynamic workflows (`Workflow` tool)

- **Script:** a JS script (`agent`, `parallel`, `pipeline`, `phase`, `log`, `args`) run in the
  background. Intermediate results stay in script variables, not in context. Limits: 16 concurrent
  agents (`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`, 1–256), 4,096 items per call, 1,000 agents per
  run. `Date.now()` and `Math.random()` throw, so replay is deterministic. (workflows §Behavior and
  limits)
- **Resume:** works in the **same session**, or after `claude --resume` or backgrounding. Completed
  `agent()` calls with unchanged prompts return cached results. The first changed or failed call, and
  every call after it, re-runs. (§Resume after a pause)
- **Opt-in:** the `ultracode` keyword, a direct request, or `/effort ultracode`. A `-p`, scheduled or
  webhook prompt does not start a workflow; `-p` and SDK runs need a `Workflow` allow rule.
  `workflowSizeGuideline` is advisory (small < 5, medium < 10, large < 50). A "Large workflow" warning
  appears above 25 agents or ~1.5M projected tokens. (§Ask for a workflow, §Cost, §Set a size guideline)
- **Classifier:** in auto mode, an `agent()` prompt "doesn't count as a request from you", so
  script-computed text never clears a soft block. (§What the saved script looks like)
- **Usage limits:** a run pauses at a usage limit only in an *interactive*, subscription-signed-in
  session. It does **not** pause in `-p`, SDK, background, Remote Control or team-mate sessions; there
  the agent fails. (§When a run hits your usage limit)

### 1.4 Hooks, scheduling, Monitor

- **Hooks:** 33 events. Exit 2 blocks on PreToolUse, Stop, SubagentStop, UserPromptSubmit,
  PreCompact, TaskCreated/Completed, WorktreeCreate/Remove and others. Exit-2 stderr reaches Claude on
  PostToolUse, Stop and SubagentStop. Exit-0 stdout becomes context only on SessionStart,
  UserPromptSubmit and PostModelSwitch. Output cap 10,000 chars. `async`/`asyncRewake` hooks.
  `SessionStart` with matcher `compact` runs after compaction and its output is added to the new
  context. (hooks; context-window §What survives compaction)
- **Scheduling:** `CronCreate`/`CronList`/`CronDelete` hold 50 tasks per session. A task fires only
  while the session runs **and is idle**, with no catch-up. Recurring tasks expire after **7 days**.
  Jitter on a 10-min task is 0–5 min. `--resume` restores cron tasks; a self-paced `/loop` and
  background Monitor tasks are **not** restored. `/loop` uses `.claude/loop.md` as its default prompt
  (project scope wins; edits apply on the next iteration; 25 KB cap). Backgrounding a session carries
  its `/loop` tasks over. Durable options: cloud routines (1 h minimum) and Desktop tasks.
  (scheduled-tasks)
- **Monitor:** streams each stdout line of a background script as an event. Docs: "often more
  token-efficient and responsive than re-running a prompt on an interval". Deadline 30 min max, then
  re-arm. (scheduled-tasks §Let Claude choose the interval; tools-reference §Monitor)
- **Cost driver:** "each scheduled fire sends full context". (costs §Why usage climbs)

### 1.5 Permission modes and the auto-mode classifier

- **Order:** deny, then ask, then allow rules, then read-only and in-workspace auto-approval, then the
  classifier. (permission-modes)
- **Default trust boundary:** "By default, the classifier trusts only the working directory and the
  current repo's configured remotes." Every other destination counts as external. Hosts whose names
  contain `prod`/`production` are *sensitive remote targets* until you name your own.
  (auto-mode-config §Define trusted infrastructure)
- **Config scopes:** `autoMode.{environment, allow, soft_deny, hard_deny, classifyAllShell}` is read
  only from **user** settings, **managed** settings and `--settings`/SDK. Project and local settings
  are ignored on purpose. An array without `"$defaults"` **replaces** that section's built-ins.
  (§Where the classifier reads configuration, §Override the block and allow rules)
- **User intent:** explicit user intent clears soft blocks, but only "if the user's message directly
  and specifically describes the exact action". Peer messages, `agent()` prompts and `-p`/scheduled
  text are not user messages.
- **Fallback:** 3 consecutive or 20 total blocks pause auto mode. In `-p` without
  `--permission-prompt-tool`, the blocked action is skipped and the run continues. (permission-modes
  §When auto mode falls back)
- **Tooling:** `/permissions` → **Recently denied** (press `r` to retry); a `PermissionDenied` hook
  receives the exact `tool_input`; `claude auto-mode config|defaults|critique`; `/auto-mode-setup`.
  (auto-mode-config §Review denials)

### 1.6 Context, compaction, handoff

- **What survives compaction:** the project-root CLAUDE.md, auto memory and the plan file are
  re-injected. Up to 5 recent files are re-read. **Invoked skill bodies are re-injected but "capped at
  5,000 tokens per skill and 25,000 tokens total"**, and "truncation keeps the start of the file". A
  `SessionStart`/`compact` hook output is added. (context-window §What survives compaction)
- **Prompt cache:** the main conversation gets a **1 h** TTL on a subscription within plan usage.
  Subagents, workflow agents and team-mates get **5 min** unless `subagentPromptCacheTtl: 1h`. A
  `/model` switch, an effort change, the first fast-mode request or a CLI upgrade invalidates the
  cache. Editing CLAUDE.md mid-session has no effect until `/clear`, `/compact` or restart.
  (prompt-caching, via the docs leg)
- **Resume:** `--resume` restores the conversation, the **model**, the agent, the permission mode
  (except `bypassPermissions` from a terminal, which needs its launch flag again), unexpired cron
  tasks and the active goal. It does **not** restore `--settings`, `--mcp-config`, `--plugin-dir`,
  `--add-dir` or `--fallback-model`. A tool call cut off by a crash is shown to Claude as cut off,
  with an instruction to check before re-running it (v2.1.281). (sessions §What a resumed session
  restores, §Permission mode on resume)
- **Handoff:** no dedicated handoff feature is documented. The primitives are `--resume`,
  `--fork-session`, `/branch`, resume-from-summary and messaging. A recommended handoff pattern:
  **UNVERIFIED**.

### 1.7 Skills, plugins, mods, MCP vs. native tools

- **Skills:** the description is always in context; the body loads on invoke. The docs say to keep
  SKILL.md under 500 lines. Frontmatter includes `model`, `effort`, `context: fork`, `agent` and
  `hooks`. (skills)
- **MCP tools:** deferred by default (tool search). Only names cost context until loaded.
  `MAX_MCP_OUTPUT_TOKENS` defaults to 25,000. Stdio servers are **not** reconnected automatically;
  remote ones retry 5 times. Scope precedence for the same name: **local > project > user > plugin >
  connector**. The whole entry wins, with no field merge, and both local and user scope live in
  `~/.claude.json`. (mcp §Scope precedence, §Automatic reconnection)
- **Mods:** JS hook modules in plugins (`tool.check` can approve a call before the classifier). On by
  default from v2.1.287 and not sandboxed. (plugins/mods, via the docs leg)

### 1.8 Notifications, model and cost routing

- **`PushNotification`:** desktop notification, plus a phone push when Remote Control is connected.
  Anthropic-hosted. The `Notification` hook fires with `agent_needs_input`/`agent_completed` from
  agent view. (tools-reference; agent-view)
- **Subagent model order:** per-call `model` > frontmatter > `CLAUDE_CODE_SUBAGENT_MODEL` > the main
  model (`…_FORCE=1` overrides). (sub-agents §Choose a model)
- **Effort:** the default is medium on Opus/Sonnet/Haiku 5.5. **Fast mode** is Opus only, "up to 2.5×
  faster", billed from usage credits even on a subscription; the first enable is a cache miss.
  (model-config, fast-mode, via the docs leg)

---

## 2. Comparison with what we built

| Ours (path:line) | What it does | Native equivalent | Verdict |
|---|---|---|---|
| `tools/autoos-agent.py` (13,146 lines; `cmd_run` :11516, `build_plan` :6247, `isolate_clone` :1620, family fence :6837–7179, free slots :2742–2962, admission :11269–11416) | Multi-vendor L3 spawn: route, isolate, fence, admit, record | Subagents/workflows (Claude-only) | **Keep.** No native multi-vendor routing. |
| `tools/autoos_agent_mcp.py` (2,224 lines; `spawn` :1133, `respond` :1789, `l2_*` :435–552) | MCP front for the spawner + L2 lane lifecycle | — | **Keep.** Opencode L2 lanes need it. |
| `tools/oc_l2.py` (1,586 lines) | Opencode L2 lane: render, health, wake (`stalled` :808) | `claude --bg` for *Claude* L2s only | **Keep** for opencode lanes. |
| `tools/autoos_recovery.py` (1,821 lines; `classify_run` :1323, `next_action` :1411) | Classify a dead or stalled L3 run, plan a continuation | Workflow stall-restart (Claude agents only) | **Keep.** Non-Claude runs. |
| `tools/autoos_inbox.py` (329) + `cmd_inbox` `autoos-agent.py:12645` + `l2_report` `autoos_agent_mcp.py:499` | File inboxes `$AUTOOS_RUN_DIR/inbox/*.md`, at-least-once read | `SendMessage` + `notify_when_idle` (Claude↔Claude) | **Hybrid.** Message = doorbell, file = durable record (no native receipt or persistence). |
| `tools/autoos-ask.py` (236) + `respond` | Worker ask-back (question.json/answer.json) | `AskUserQuestion` (main only); background subagents lack it | **Keep** for non-Claude workers. |
| `tools/autoos_heartbeat.py` (697) + `heartbeat_state` `autoos-agent.py:7322` | Read-only pause/unpushed/dirty report | — (repo-specific) | **Keep.** Call it from a `/loop` prompt. |
| R-coord-07/08 beat (`references/main-orchestrator.md:20`, cron `7-59/10`) | 10-min `CronCreate` beat per orchestrator | Same `CronCreate`, plus `.claude/loop.md`, `Monitor`, `notify_when_idle` | **Adopt partly** (C3). |
| `tools/autoos_context.py` (212) | Context fill from transcript JSONL | `/context` (interactive); no documented hook value (**UNVERIFIED**) | **Keep.** |
| `run_handoff_sessions.ps1` `Wait-Bg` :583–626, `Test-Stalled` :564, `Get-TreeCpuSeconds` :518; `HandoffCore.psm1` `Test-HandoffPermissionPrompt` :351–360, `Test-HandoffStalled` :902 | Watch background sessions by polling `claude agents --json` `state` + log/CPU/tree heuristics; scrape text for permission prompts | `claude agents --json` `status` + `waitingFor` (`permission prompt`, `input needed`, `dialog open`); `notify_when_idle`; `Notification` hook | **Adopt** (C4). The runner already uses `--bg` (`run_handoff_sessions.ps1:340`). |
| `HandoffCore.psm1` `Classify-HandoffFailure` :284–349 | Usage-limit / auth / transient classification and wait | Interactive auto-continue only. Background and `-p` don't wait (workflows §usage limit). | **Keep.** |
| `trust_worktree.py` (621) | Pre-trust worktrees + MCP approval for background sessions | Background dispatch makes its own worktree; `claude agents` asks for trust once per workspace (v2.1.225) | **Keep until measured** (Q-5). |
| `l1_handoff.py` (160) | Successor prompt + machine snapshot | `--resume`, `--fork-session`; no native handoff | **Keep.** Feed the snapshot from `claude agents --json`. |
| Skill rule "re-read skill after compaction" (D-971); `SKILL.md` = 26,330 bytes (~6.5k tokens) | Manual rule recall | `SessionStart` hook, matcher `compact`; skill re-inject cap 5k tokens | **Adopt** (C2). |
| Router `QUESTIONS.md` / `needs_you` / `BLOCKED` lines (D-942; private router repo) | Durable question record, polled | `PushNotification` / `Notification` hook as doorbell | **Hybrid** (C6). Keep the file as the record. |
| CLAUDE.md "MCP scope trap" section | Claims user scope overrides project `.mcp.json` | Docs: **local > project > user**; *local* is also stored in `~/.claude.json` | **Fix doc wording** (C10, Q-8). |

---

## (a) Do / don't

**Do**
- Start every unattended Claude orchestrator with explicit inbound settings, e.g.
  `--settings '{"crossSessionInbound":"accept"}'` (or `"dialogExpiry":"never"`). Don't rely on the
  per-mode default; bypass receivers hold messages by design.
- Name sessions uniquely per run (`--name autoos-L2-<lane>-<date>`), and copy the `[ref]` from
  `ListAgents` when a name collides.
- Treat a message as a doorbell. Write the durable fact (REPORT/DONE/question) to a file or commit
  first, then message its path.
- Use `notify_when_idle` (one-shot, free for the watched session) instead of "are you done?" pings or
  polling.
- Watch child artefacts (`exit.json`) with `Monitor` + an `until`/`inotifywait` script, not with a
  beat that re-reads everything.
- Put must-survive rules in a `SessionStart`/`compact` hook, or at the top of SKILL.md (truncation
  keeps the start).
- Keep `autoMode` only in user or managed settings, always with `"$defaults"`. Name own hosts in
  `environment` and production hosts in "Sensitive remote targets".
- Use `permissions.ask`/`deny` for hard boundaries. Conversation-stated boundaries can be lost to
  compaction.
- Capture denials with a `PermissionDenied` hook into a log. That records refusals verbatim, as
  R-orch-08 asks.
- Use `claude agents --json` `status`/`waitingFor` to detect blocked background sessions.

**Don't**
- Don't assume a successful `SendMessage` was read: no receipt exists.
- Don't send messages from a non-bypass session to a bypass session without `accept`. The message is
  held, and dropped after 5 min in `-p`/IDE sessions.
- Don't count on queued messages surviving a restart, `/clear` or compaction of a non-background
  session (undocumented).
- Don't expect peer messages, `agent()` prompts or `-p` text to count as "explicit user intent" to the
  classifier.
- Don't put `autoMode` in `.claude/settings*.json`: it is ignored. Don't commit host names: the repo
  is public.
- Don't define one MCP server name in two scopes, and don't forget *local* scope also lives in
  `~/.claude.json`.
- Don't switch model or effort mid-session in a long orchestrator; it is a cache miss.
- Don't enable agent teams for unattended runs: interactive only, ~7× tokens.
- Don't rely on workflow usage-limit pausing in background or `-p` sessions.
- Don't use `/loop` self-paced mode as the only beat: it is not restored on `--resume`.

---

## (b) Adoption candidates, ranked by expected saving

Estimates are order-of-magnitude, marked *est.*, and derived from the cited docs plus our measured
incidents. None was measured in this lane.

| # | Candidate | Saving (time / tokens / money) | Evidence | Effort |
|---|---|---|---|---|
| **C1** | **Explicit inbound policy for orchestrators**: `crossSessionInbound: accept` via `--settings` at launch, plus unique names. | **High time.** Removes the held/dropped-message class ("lost messages") and its re-sends and re-briefs. *est.* 1–3 lost-message incidents per run avoided, each 15–60 min of a stalled lane. | cross-session-messaging §Control inbound messages: bypass receivers hold by default; `-p` holds expire after 5 min. Our orchestrators run `from-mode="bypass"`. | Config only |
| **C2** | **`SessionStart` hook, matcher `compact`**, re-injects a ≤4k-token rules card; plus reorder SKILL.md so level rules come first. | **High reliability, medium tokens.** Ends rule drift after compaction, replaces the manual D-971 re-read (~6.5k tokens per compaction) and the defects caused by forgotten rules. | context-window §What survives compaction: skill re-inject capped at 5,000 tokens, start kept. Our SKILL.md is 26,330 bytes. | Hook + small card file |
| **C3** | **Event-driven waiting**: `notify_when_idle` for child sessions; `Monitor` for `exit.json`/inbox files; keep a 10-min `CronCreate` beat only as a fallback, with its prompt in `.claude/loop.md`. | **High tokens.** "Each scheduled fire sends full context." *est.* At 150k context and 6 beats/h, ~0.9M cache-read tokens/h per idle orchestrator; event wakes drop most idle beats. The beat prompt moves from every brief into one file. | costs §Why usage climbs; scheduled-tasks §Let Claude choose, §Limitations; cross-session-messaging §notify | Skill text + loop.md |
| **C4** | **Runner uses native background-session state**: read `status`/`waitingFor` from `claude agents --json`; add a `Notification` hook (`agent_needs_input`) instead of text-scraping permission prompts and CPU/log stall heuristics. | **Medium time and code.** Blocked sessions are detected in seconds instead of after a stall window. ~150–250 PS lines become deletable after a measured run. | agent-view `--json` fields, §Notification; changelog v2.1.205 (state no longer flips back to Working on empty turns), v2.1.212 (`waitingFor` precision) | Runner change + test |
| **C5** | **`autoMode.environment` in user settings** (private; never in the repo) naming own hosts, remotes and services, with production targets in the sensitivity slot; plus a `PermissionDenied` logging hook. | **High time on remote-host lanes.** Fewer classifier blocks, `BLOCKED` lines and operator round-trips. Denials become reviewable data. | auto-mode-config §Define trusted infrastructure ("trusts only the working directory and the current repo's configured remotes"), §Review denials | Operator-only (permission settings) |
| C6 | **Push as doorbell for operator questions**: a `Notification`/`PushNotification` when a `Q:`/`BLOCKED` line is appended. The file stays the record. | Medium time (operator latency) | tools-reference §PushNotification (phone needs Remote Control) | Operator choice |
| C7 | **Native read-only research fan-out** (`/deep-research`, a small workflow with Sonnet/Haiku agents) for doc-reading legs like this one | Medium time; costs Claude-plan tokens | workflows §Bundled workflows, §Cost | Conflicts with the Claude-budget policy (Q-6) |
| C8 | **Claude Code harness on gateway legs** for L3 (`claude -p`/`--bg` with `ANTHROPIC_BASE_URL` → gateway `/v1/messages`). Native hooks, skills, worktrees and messaging on free legs. | Potentially high, long-term (could retire opencode-specific traps, `autoos-agent.py:7–33`) | OS-30 measured `/v1/messages` and tool fidelity on one free leg (`docs/plans/2026-09-28-fallback-fleet-spec.md:289-290`); other legs **UNVERIFIED** | Experiment first (Q-7) |
| C9 | **Subagent cache TTL**: `subagentPromptCacheTtl: 1h` for long Claude subagent fan-outs | Low–medium tokens (cache re-writes after 5 min idle) | prompt-caching §Choose the TTL (via the docs leg) | Config |
| C10 | **Correct the CLAUDE.md MCP scope wording** to the documented precedence (local > project > user). Keep the detector query. | Low (prevents misdiagnosis) | mcp §Scope precedence | Doc edit |

---

## (c) Custom code that could be deleted (after each migration is measured)

| Path:lines | What | Replaced by | Condition |
|---|---|---|---|
| `.agents/skills/unattended-orchestration/HandoffCore.psm1:351-360` `Test-HandoffPermissionPrompt` (+ callers) | Regex scrape for permission/trust dialogs | `claude agents --json` `waitingFor = permission prompt / dialog open` | C4 measured on one live lane |
| `run_handoff_sessions.ps1:518-582` `Get-TreeCpuSeconds`, `Test-Stalled` and the CPU-epoch part of `Wait-Bg` :583-626 | Hung-session heuristics | `status` busy/waiting/idle + `notify_when_idle`/`Notification` hook | C4; keep `Test-DoneQuiet` (DONE-note semantics are ours) |
| `HandoffCore.psm1:902-943` `Test-HandoffStalled` | Same, in the core module | Same | C4 |
| Beat prose repeated in briefs and `references/main-orchestrator.md:20-24` | Beat instructions | `.claude/loop.md` (one file) | C3 |
| D-971 "re-read skill after compaction" rule text in skill and briefs | Manual recall | `SessionStart`/`compact` hook | C2 |
| `AgentAdapters.psm1:25-33` `resumeModelNote` "UNVERIFIED" comment | Open question: does `claude --bg --resume` keep the model? | Docs answer it: "Model: the session continues on the model it was using" (sessions §What a resumed session restores) | Now; a comment-only change (one live check recommended) |

Rough total: ~250–400 lines of PowerShell plus repeated brief prose. **Not deletable:** the spawner,
resolver, recovery, `oc_l2.py`, inbox files (opencode lanes still need them), `autoos-ask.py`,
`trust_worktree.py` (until Q-5) and `Classify-HandoffFailure` (background sessions don't wait out
limits).

---

## (d) Risks

| Risk | Applies to | Mitigation |
|---|---|---|
| Agent view is a **research preview**: flags and behaviour change between releases (dozens of changelog rows since v2.1.139) | C4, C1 | Pin the measured CLI version in the runner check; keep a fallback path for one release |
| `accept` lets any local same-user session inject text into a bypass orchestrator | C1 | Same-user socket only (docs); peers can't approve prompts; keep `isolatePeerMachines: true` |
| No delivery receipt; messages are not durable | C1, C3 | File-first, message-second (the doorbell rule) |
| Cron fires only while idle; 0–5 min jitter; 7-day expiry; a background session with no turn for ~1 h is stopped (whether a cron beat keeps it alive: **UNVERIFIED**) | C3 | Keep the cron beat as fallback; `Ctrl+T` pin for orchestrators; measure on one lane |
| `autoMode` misconfiguration (missing `"$defaults"`) silently drops built-in blocks | C5 | Always `"$defaults"`; verify with `claude auto-mode config`; operator applies it |
| Host names in settings must never reach the public repo | C5 | User scope only; nothing in `.claude/` of this repo |
| Hook output cap of 10k chars; a hook that fails silently gives no rules | C2 | Card ≤4k tokens; a test asserts the hook prints it |
| Gateway-backed harness: other legs may break tool fidelity | C8 | Per-leg probe as in OS-30 before routing anything |
| Claude-plan tokens for native fan-out | C7 | Only with an operator yes; `small` guideline |

---

## (e) Migration steps and (f) rollback, per candidate

**C1 — inbound policy**
1. In the claude adapter's `start` **and** `resume` argv
   (`.agents/skills/unattended-orchestration/AgentAdapters.psm1:11-12`), add
   `--settings '{"crossSessionInbound":"accept","isolatePeerMachines":true}'` and a unique `--name`.
   It must go on both, because "`--settings` … pass them again when you resume" (sessions §What a
   resumed session restores).
2. Add one line to the skill: message = doorbell, file = record.
3. Measure: send from a prompting session to a bypass L2 and confirm `Delivered` and that no approval
   dialog appears.

Rollback: remove the `--settings` argument; the per-mode default returns.

**C2 — compact hook**
1. Write a ≤4k-token rules card (`.agents/skills/unattended-orchestration/CARD.md`) from SKILL.md's
   level rules.
2. Add a `SessionStart` hook with matcher `compact` that `cat`s the card. The installers link it like
   the skills.
3. Move the level rule tables to the top of SKILL.md.
4. Test: force `/compact` in a scratch session and assert the card text is in context.

Rollback: delete the hook entry; the card file is inert.

**C3 — event-driven waiting**
1. Add `.claude/loop.md` with the R-coord-08 beat checklist.
2. Change R-coord-07: "fallback beat via `CronCreate` (or `/loop 10m`)". Add a new rule: wait on
   children with `notify_when_idle` (sessions) and `Monitor` on `exit.json` (L3 runs).
3. Measure tokens per idle hour before and after on one L2, with `autoos-agent.py token-rate` or
   `/usage`.

Rollback: restore the old rule text; delete `loop.md`.

**C4 — runner on native state**
1. Extend `Get-BgEntry`/`Wait-Bg` to read `status`/`waitingFor`; map `waitingFor` to a `blocked`
   result.
2. Add a Pester test with a recorded `--json` fixture.
3. Run one live lane with both paths logging. When they agree for one run, delete the heuristics in
   (c).

Rollback: `git revert` of the runner commit; the heuristics are back.

**C5 — trusted infrastructure (operator)**
1. The operator runs `/auto-mode-setup`, or edits `~/.claude/settings.json` `autoMode.environment`
   with `"$defaults"` and own hosts, then runs `claude auto-mode config`.
2. Add a `PermissionDenied` hook that appends `tool_input` + reason to a private log.

Rollback: `claude auto-mode reset`; remove the hook.

**C6 — push doorbell (operator)**
1. A `Notification` hook or one `PushNotification` call when a `Q:`/`BLOCKED` line is written.

Rollback: remove the hook.

**C7 — native research fan-out (operator, budget)**
1. A saved `.claude/workflows/research.js` with Haiku/Sonnet agents and the `small` guideline.

Rollback: delete the file.

**C8 — gateway harness (experiment)**
1. Repeat the OS-30 probe per candidate leg.
2. Add a `claude` client mode in `autoos_clients.py` that passes `ANTHROPIC_BASE_URL` via
   `--settings` env, with a family fence on the gateway model.
3. Run one leg in parallel with opencode on the same task.

Rollback: drop the client mode; routes unchanged.

**C9:** add the `subagentPromptCacheTtl` setting; rollback: remove it. **C10:** a one-paragraph doc
edit; rollback: revert.

---

## (g) Open questions for the operator

- Q-1: C1 inbound policy for unattended orchestrators? Options: a=leave the per-mode default; *b=`accept` + `isolatePeerMachines:true` via launch `--settings`; c=`dialogExpiry:"never"` only (hold without dropping)
- Q-2: C2 rules-card compact hook? Options: a=no, keep the manual re-read; *b=hook + card ≤4k tokens + reorder SKILL.md
- Q-3: C3 heartbeat model? Options: a=keep the 10-min cron beat only; *b=event-driven (`notify_when_idle` + `Monitor`) with the cron beat as fallback; c=self-paced `/loop` only
- Q-4: C5 trusted-infrastructure config (permission settings = operator-only)? Options: a=no change; *b=operator runs `/auto-mode-setup` in user scope + a `PermissionDenied` log hook; c=managed settings
- Q-5: Retire `trust_worktree.py` for background-session lanes? Options: *a=keep until a measured run shows no trust/MCP dialog in a fresh dispatched worktree; b=retire now
- Q-6: C7 allow Claude-plan tokens for native read-only research fan-out? Options: *a=no, keep non-Claude L3 legs (budget policy); b=yes, Haiku/Sonnet, `small` guideline
- Q-7: C8 gateway-backed Claude Code harness for L3? Options: a=no; *b=per-leg probe experiment on one lane; c=adopt broadly
- Q-8: C10 correct the CLAUDE.md MCP-scope wording to the documented local > project > user? Options: *a=yes (doc-only, keep the detector); b=no, re-measure first
- Q-9: C6 phone push for `Q:`/`BLOCKED` lines (needs Remote Control)? Options: a=no; *b=desktop notification only; c=desktop + phone

---

## Appendix: unverified items

- Fate of queued cross-session messages on restart, `/clear` or compaction of a non-background session.
- Whether a recurring `CronCreate` beat keeps a background session's process from the ~1 h idle stop.
- Whether background sessions auto-wait at a usage limit (workflows say background *workflow agents*
  don't pause).
- Context-fill value exposed to hooks or the status line (would retire `autoos_context.py`).
- Gateway `/v1/messages` fidelity for legs other than the one OS-30 measured.
- Per-tool token cost of deferred MCP tools (docs page truncated in both reads).
