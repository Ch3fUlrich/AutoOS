# HOOKS — one guard/context source, rendered per client (SPEC ONLY)

Owner: L2-general. Status: SPEC (no implementation).
Budget mode: writers and first reviewers are free models; Claude does finals only.

## 1. Summary

AutoOS discipline today is prompted, not enforced: the D-080 restart ritual
(brief + status card + shared-context tail + inbox tail on every relaunch),
the 10-minute heartbeat cron, the PATH/secret/config guards, and the
`status/<name>.md` state card are conventions an orchestrator follows because
briefs and skills say so. Claude Code's hook system (shell commands the
harness runs at session/tool/compact/stop lifecycle points, able to block a
tool call or inject context) can turn several of these into code the harness
runs. The design: ONE source of truth for "what context to inject at start"
and "what to guard against", rendered per client — not nine near-identical
guard scripts. `lib/agent_harness.py` already does exactly this for MCP
servers, fences and roles from `catalog/agent-harness.json`; this spec extends
that file and that module with a `hooks`/`guards`/`startup_context` section
and one renderer per client. Hooks are additive: they coexist with today's
manual D-080 practice and change no existing subcommand.

## 2. Claude Code hooks inventory

Exact vendor JSON schemas, exit-code contracts and matcher syntax were
(unverified - no web access) at write time; the Sonnet final (this pass)
checked the 9 hooks below against `code.claude.com/docs/en/hooks` and
corrected two rows (PreToolUse/Stop exit-code contracts now confirmed;
PreCompact's context-injection claim corrected — see its row). Every AutoOS
use cites a real file.

| Hook | Fires on / can do | AutoOS use or no use |
|---|---|---|
| SessionStart | Session begins (incl. resume/clear, per matcher). Can inject context (additional context JSON) and run setup commands; cannot block anything (session already started). | USE (P1): inject the D-080 restart bundle — brief + state-card NEXT/threads + shared-context tail + inbox tail. Today's closest checked-in artifact is the RESTART pack: card `<RUN>/status/<name>.card.md` (`docs/plans/2026-09-28-restart-spec.md:132-163`), pack order prefix/snapshot/memory/brief/card/events (`docs/plans/2026-09-28-restart-spec.md:172-211`). The hook renders "run `pack --since-card`, print it" instead of trusting the relaunched session to remember. |
| UserPromptSubmit | Every user prompt, before the model sees it. Can inject context or block/redirect. | NO USE for an unattended orchestrator — stated explicitly: there is no interactive user; prompts arrive as briefs, inbox records and heartbeat beats, none of which pass through this hook. A prompt-time guard would fire on the orchestrator's own machinery, not on operator intent. Revisit only for attended workstation sessions. |
| PreToolUse | Before a tool call; matcher per tool. Can block via `exit 2` or `hookSpecificOutput.permissionDecision: "deny"` (both confirmed, Sonnet final vs vendor docs); either also carries `hookSpecificOutput.additionalContext`. | USE (P1): the three §6 guards — git-push-to-main block; secret-path bash deny; self-spawn depth block. See §6 for what is new vs already code-enforced. |
| PostToolUse | After a tool call completes. Observe/inject only; cannot undo. | USE (P1, lint tier only): `shellcheck` after a `.sh` edit, `python3 -m py_compile` after a `.py` edit. Observe-only feedback, never a block: a failing lint is injected as context for the next turn, matching the repo rule that shell must be `shellcheck` clean (`AGENTS.md:100-102`). |
| PreCompact | Before context compaction summarizes history away. Observational: runs a command as a side effect; no documented context-injection field for this hook (Sonnet final, verified against vendor docs — corrects the brief's "inject its output" phrasing). | USE (P1): write/refresh the state card before it is summarized away — the card at `docs/plans/2026-09-28-restart-spec.md:132-163` plus the `RUN/status/<name>.md` wave-rewrite procedure (`.agents/skills/unattended-orchestration/references/state-file.md:38-44`). The P1 use only needs the run-a-command side effect (writing the card file), not injection, so this correction does not change the design. Last chance to persist `threads`/`traps` as facts, not as summary prose. |
| Stop | The session's main loop ends (agent finished). Can block via `exit 2` (forces continuation, confirmed Sonnet final vs vendor docs) or run exit capture. | USE (P1): same state-card write as an exit-time safety net. Cheaper than a WIP commit, needs no git, covers the case the session never hits its context cap (`R-coord-06`, `.agents/skills/unattended-orchestration/SKILL.md:106`). |
| SubagentStop | A subagent (dispatched worker) ends. Runs in the parent context; can inject the child's closing state. | USE (P2, not P1): a dispatched worker's exit point could feed the card/log write workers never get today — they only get `job.json`/`output.log`/`exit.json` (the run-dir layout `tools/autoos_agent_mcp.py` documents for spawned runs). P2 because leaf output contracts already exist (`catalog/agent-harness.json:9`, `docs/agents/leaf-contract.md` via `rules.leaf_contract`), so this is a nicer capture path, not a missing one. |
| SessionEnd | Session fully ends/cleanup. Observe/cleanup only. | MARGINAL: cleanup, e.g. release a future fleet-node lease (forward hook only; this spec does not design fleet-node). No other AutoOS use: heartbeats already own liveness (`R-coord-07`, `.agents/skills/unattended-orchestration/SKILL.md:107`), and run state lives in git-ignored `logs/` (`tools/autoos_clients.py:393-402`), which needs no session-scoped cleanup. |
| Notification | The harness surfaces a blocked/waiting state (permission prompt, long wait). Observe/notify only. | COMPLEMENT, not duplicate, of `tools/autoos-ask.py`: the ask-back writes `question.json` (`{"text", "asked"}`, `tools/autoos-ask.py:19-24`) and blocks until `respond()` writes `answer.json`, with exit codes 0/2/3/5 (`tools/autoos-ask.py:39-44`). That is worker→orchestrator signalling with a persisted, auditable artifact. Notification is harness→human surfacing with no artifact. Wire Notification to *announce* a pending `question.json` (nudge the operator), never to replace it: the file is the state, the notification is the bell. |

Correction to the brief: SessionEnd is described as "cleanup, e.g. release a future
fleet-node lease" — kept, but scoped as forward-only. No other correction: the
brief's nine-hook list matches the nine rows above one for one.

## 3. Per-client equivalents

Client matrix ground truth in this checkout: `tools/autoos_clients.py:51-74`
(`CLIENTS` — opencode gateway+headless+subagents; claude headless+subagents
without gateway; qwen/gemini/codex via `omniroute run` (qwen with subagents,
gemini/codex without); agy headless-only without gateway or subagents; qoder
headless+subagents without gateway, own-account login). Skills-dir wiring per client: `AGENTS.md:269-276` (Claude Code
`~/.claude/skills`, gemini/qoder/qwen `~/.agents/skills`, codex
`~/.codex/skills`, OpenHands `~/.agents/skills` + `~/.openhands/skills`,
opencode project `.agents/skills`). Everything beyond these two citations in
this section is (unverified - no web access) — field names, file names and
hook points are sketched from the brief, not from vendor docs.

- opencode: `tool.execute.before` / `tool.execute.after` confirmed (Sonnet
  final vs `opencode.ai/docs/plugins/`), plus real session-lifecycle events
  `session.created`, `session.updated`, `session.compacted`, `session.idle`,
  `session.status`, `session.error`, `session.deleted`, `session.diff` (same
  source) — `session.compacted` is opencode's PreCompact-equivalent hook
  point, `session.idle`/`session.created` cover Stop/SessionStart-equivalent
  timing. Message-level (`message.updated`, `message.part.*`) and
  `shell.env` hooks also exist but have no P1 AutoOS use. Still to confirm in
  the P-lane: exact payload shapes for each event (this pass verified event
  *names* only, not full field schemas) and how a plugin's guard denies a
  tool call (docs list the hook points but this pass did not fetch the deny
  contract). What IS also verified: the existing single
  source is `catalog/agent-harness.json` (`rules` `catalog/agent-harness.json:4-10`,
  `fences` `catalog/agent-harness.json:11-73`, `mcp_servers`
  `catalog/agent-harness.json:74-119`, `roles` `catalog/agent-harness.json:120-199`),
  and the `opencode` subcommand merges it into an OpenCode config with
  fence-wins merge (`lib/agent_harness.py:305-381`, `_rebuild_patterns`
  `lib/agent_harness.py:218-238`). Depth precedent: `experimental.subagent_depth`
  (`opencode.jsonc:72-75`, mirrored by `DEFAULT_MAX_DEPTH`
  `tools/autoos_clients.py:32`). Leaf spawn precedent: the managed opencode
  config denies `subagent` and `autoos-agent_*` for leaves
  (`opencode.jsonc:100`, `opencode.jsonc:174-176`).
- OpenHands: brief asserts setup script, microagents, repo instructions as the
  nearest equivalents (unverified - no web access; real names to be confirmed
  in the P-lane). What IS verified: the `vendor` subcommand renders vendored
  profiles from `openhands/agent-profiles/` (`lib/agent_harness.py:673-709`),
  `openhands` merges into an installed `~/.openhands` tree
  (`lib/agent_harness.py:745-775`), roles carry `openhands.profile` +
  `llm_profile_ref` (`catalog/agent-harness.json:129-132`), and
  `render_profile` appends a leaf/spawn `system_message_suffix`
  (`lib/agent_harness.py:617-639`, suffix `lib/agent_harness.py:612-614`).
  OpenHands has no PreToolUse-style block point in this checkout's model —
  enforcement there is instructions + profile shape, so guards render as
  setup-script checks and microagent rules, not as blocks.
- gemini CLI and codex CLI: nearest equivalent is the instructions file each
  CLI reads plus CLI flags the spawner already passes
  (`tools/autoos_clients.py:129-164`: gemini `--approval-mode`, codex
  `--sandbox`, both via `omniroute run`). No `.agents/skills/` client-mapping
  references for gemini/codex hook equivalents were found (no
  `codex-tools.md` / `gemini-tools.md` exist under `.agents/skills/` —
  verified by directory listing: only `coding-principles`,
  `unattended-orchestration`, `structured-memory`, `swarm-orchestration`,
  `qa-swarm`, `review-triage`, `babysit-prs`, `no-mistakes`,
  `repository-index`, `html-working-documents`, `homelab-access`,
  `herdr-orchestration`, `mcp-servers-setup`). Both clients run with
  `subagents: False`, so there is no subagent-stop surface at all. Anything
  beyond instructions-file + flags is (unverified - no web access).
- D-102 budget mode / D-100 WSL2-default (brief context): no rendering
  difference — budget picks the model (`route`), hooks pick the guard. The
  same rendered guard runs regardless of which tier's tokens paid for the
  turn. WSL2 matters only in that rendered shell guards must be bash-safe
  (the suite already runs there per `AGENTS.md:146-151`).

## 4. Shared data shape

Lives in `catalog/agent-harness.json` as three new top-level keys
(`hooks`, `guards`, `startup_context`) — EXTEND, not a sibling file.
Justification (coding-principles P1, one home per fact): fences, roles, MCP
servers and rules already live in this one file (`lib/agent_harness.py:2-14`
module docstring: "One generator"); a sibling `catalog/agent-hooks.json`
would duplicate the `TOP_LEVEL_KEYS` / `validate()` pattern
(`lib/agent_harness.py:30`, `lib/agent_harness.py:45-143`) and split "what a
spawned session may do" across two files that must stay in lockstep (a guard
naming an MCP server the other file renamed). Size objection answered: the
new keys hold data (patterns, commands, budgets), not scripts — scripts stay
in `lib/` next to the renderers, same as fences hold patterns while
`desired_opencode` holds the merge logic.

Fields (data shape, not a full JSON schema):

- `guards`: list of `{id (stable kebab-case, never reused — same contract as catalog `id`, `AGENTS.md:90`), kind: push-to-main | secret-path | self-spawn | lint, scope: all | leaf, fence_refs: [...] (names of `fences` keys, e.g. `bash_deny_all` — `fences` stays the single home for patterns, coding-principles P1), extra_patterns: [...] (default empty; genuinely hook-only patterns with a justification each), hook_points: [PreToolUse, ...], enforcement: block | context-only, provenance: "already code-enforced; hook adds defense in depth" | "new at the hook layer"}`. The secret-path guard's `fence_refs` names the fence lists holding the KEYDENY set (`catalog/agent-harness.json:31-44` shell side, `:59-67` read side). The renderer resolves names to patterns at render time; the validator checks that every referenced name exists — never that two pattern lists are equal. The subset invariant (§4, next bullet) then covers only `extra_patterns`, if any.
- `startup_context`: `{bundle: [{item: card | brief | shared-tail | inbox-tail, scope: all | leaf}...], pack_cmd: "autoos-agent.py pack <name>", budget_tokens: 8000 (mirrors the RESTART variable budget `docs/plans/2026-09-28-restart-spec.md:207-211`), hook_points: [SessionStart], leaf_budget: none (default)}`. Scope defaults to `all`; the renderer skips `scope: all` items for leaf-role sessions. Leaves default to `leaf_budget: none` — a leaf's one closed task is inlined in its brief instead (`docs/agents/leaf-contract.md:7-12`), so injecting the ≤8000-token D-080 bundle would drown it. A leaf entry may opt into `minimal` (card header only) with a stated reason, never the full bundle.
- `hooks`: `{session_start: {inject: startup_context.bundle}, pre_compact: {run: "card refresh"}, stop: {run: "card refresh"}, subagent_stop: {run: "capture to card/log (P2)"}, session_end: {run: "release holds (forward-only)"}, notification: {run: "announce pending question.json"}}` — one entry per §2 row with a use; `UserPromptSubmit` is present with `use: false` and the reason, so a future reader does not re-litigate it.
- `validate()` gains: `hooks`/`guards`/`startup_context` presence checks; `git push*` in exactly one fence list invariant KEEPS working (`lib/agent_harness.py:59-65`); guard `extra_patterns` carry a new-with-justification marker each (a hook-only pattern with no justification is a defect — if it belongs on every client it belongs in `fences` instead); every `hook_points` entry names a §2 hook; every `fence_refs` entry names an existing `fences` key; every `startup_context` bundle item carries a `scope`.

## 5. Renderers

Extend the existing subcommand structure (`build_parser`,
`lib/agent_harness.py:781-816`); do not replace it. New subcommands:

- `claude-hooks --out <dir>`: emits a `.claude/settings.json` `hooks` block
  (matchers per §2 rows; commands are thin shims calling back into
  `tools/autoos-*.py`, e.g. SessionStart runs the pack command, PreToolUse
  runs the guard script with the tool JSON on stdin — shims inspect the input
  and exit zero/non-zero (or the vendor deny contract); they never log or echo
  the full input, which may carry secrets, `AGENTS.md:16-21`) plus the guard scripts
  themselves under `<out>/hooks/` (bash, `set -euo pipefail` per
  `AGENTS.md:100-102`). Role identity comes from a spawner-set `AUTOOS_AGENT_ROLE`
  env var (`leaf` | `all`, derived from the role's `leaf` flag,
  `catalog/agent-harness.json:120-199`) that the guard shims read — same shape
  as the existing depth env (`AUTOOS_AGENT_DEPTH`/`AUTOOS_AGENT_MAX_DEPTH`, set
  `tools/autoos-agent.py:1844`, read `tools/autoos_clients.py:376-390`).
  `.claude/settings.json` is per-directory so it cannot carry per-agent role;
  the env var can. Unknown or absent role defaults to `leaf` (scope-down: deny),
  so a misconfigured session fails closed toward the leaf guard, never toward
  allow-everything. Merge convention follows `cmd_opencode`
  (`lib/agent_harness.py:533-588`): backup-then-write (`_backup_and_write`,
  `lib/agent_harness.py:482-530`, hard rule 5 `AGENTS.md:31-32`), refuse
  symlinks, `--dry-run` prints `would update/install`, unchanged prints
  `skipped` (idempotency `AGENTS.md:113-124`). A pre-existing
  `.claude/settings.json` with its own hooks is merged, never overwritten
  wholesale (hard rule 4, `AGENTS.md:28-30`): hook entries for other tools are
  kept byte-for-byte; AutoOS-managed entries are identified by a marker key
  naming the guard id (exact field confirmed in the P-lane against the installed
  vendor schema) and rewritten in place; the write goes through
  `_backup_and_write` (`lib/agent_harness.py:482-530`); a symlinked settings
  file is refused rather than followed.
- `opencode-hooks --config <path>`: emits an opencode plugin (or plugin
  config — P-lane picks after verifying real field names against the
  installed opencode; until then the shape is TBD, marked in code) carrying
  the same three §6 guards mapped onto `tool.execute.before` and the lint
  tier onto `tool.execute.after`, plus session-start injection of the pack
  command. Reuses `_role_bash` (`lib/agent_harness.py:268-277`) so leaf vs
  all scoping matches the permission merge byte-for-byte.
- `openhands-hooks --openhands-dir <dir>`: emits setup-script content (guard
  checks that fail the container build when violated) and microagent/instructions
  content (startup bundle + card-write discipline as a `system_message_suffix`
  extension, following `render_profile`, `lib/agent_harness.py:617-639`).
  No block semantics — documented in the emitted text, not asserted in code.
- `gemini-codex --out <dir>`: emits the instructions-file equivalent (role
  brief + guard list as prose the CLI reads) and the flag set the spawner
  already passes (`tools/autoos_clients.py:129-164`). Thinnest renderer by
  design: where the client offers no hook point, the renderer says so in its
  output header instead of faking enforcement.

All four renderers read the same three keys from §4; a guard added once
appears on every client that has a hook point for it, and the per-client
subsections above name what each client cannot express.

## 6. Guards in scope for P1

| Guard | Source in this checkout | Verdict |
|---|---|---|
| git-push-to-main block | Fences deny `git push*` at LEAF scope only (`catalog/agent-harness.json:46-57`; validator pins push to exactly one fence list, `lib/agent_harness.py:59-65`; operator 2026-09-22 comment `lib/agent_harness.py:59-61` says the top-level session may push). Pattern evidence in the repo's own config (not `desired_opencode` output — that file is the checked-in t1/t2/t3 config, rendered output comes from `desired_opencode`, `lib/agent_harness.py:305-381`): the leaf-shaped t3 agent denies `git push*` / `*git *push*` (`opencode.jsonc:142-143`). | Already code-enforced via the harness merge; hook adds defense in depth AND closes the Claude-Code gap (Claude Code never reads the opencode permission merge, so without a PreToolUse hook the guard does not exist there at all). Hook guard reads `AUTOOS_AGENT_ROLE` (§5) to reproduce the leaf/all split: `leaf` blocks push, `all` allows the orchestrator's own push lane. |
| secret-path bash deny | KEYDENY fence: shell denies `*api_keys*`, `*api-keys*`, `*client.key*`, `*manage.key*`, `*.env*`, `env`, `printenv*`, `*.claude.json*`, `*opencode*` paths (`catalog/agent-harness.json:31-44`, read side `59-67`); rendered (`opencode.jsonc:128-141`); KEYDENY/KEYDENY2 history (`CHANGELOG.md:221-236`); example-file allows (`catalog/agent-harness.json:69-72`). | Already code-enforced for opencode-managed sessions; hook adds defense in depth and portability to Claude Code. New at the hook layer ONLY in the sense that no `.claude/settings.json` guard exists today — the pattern set itself is referenced by fence name from `fences` (§4 `fence_refs`), resolved at render time, never re-authored. |
| self-spawn depth block | `child_depth` refuses past the max (`tools/autoos_clients.py:376-390`, `DEFAULT_MAX_DEPTH = 2`, `tools/autoos_clients.py:32`); the MCP `spawn` maps it to a refusal, never a launch (`tools/autoos_agent_mcp.py:374-402`, depth check `tools/autoos_agent_mcp.py:327`); `R-worker-06` binds leaves (`SKILL.md:136`). | Already code-enforced at the spawner; hook adds defense in depth for the path that bypasses the spawner (a session calling the client's native subagent/task tool directly instead of `autoos-agent spawn` — the exact hand-roll `R-coord-09` forbids, `.agents/skills/unattended-orchestration/SKILL.md:109`). The hook cannot know the true depth count (the spawner's `AUTOOS_AGENT_DEPTH` does not track the client's native nesting), so for `AUTOOS_AGENT_ROLE=leaf` (§5) it denies native-spawn outright — same shape as the opencode `subagent: deny`, `opencode.jsonc:100` — and logs the attempt for the orchestrator; `all` sessions log-and-allow. |

## 7. Context injection in scope for P1

- SessionStart → D-080 restart bundle. The hook runs the RESTART pack
  (`pack`, `docs/plans/2026-09-28-restart-spec.md:172-211`) and injects the
  result: card (§1, `docs/plans/2026-09-28-restart-spec.md:132-163`) +
  snapshot + brief + `inbox --since-card` events. Budget: variable part
  ≤8000 tokens estimated bytes÷3 (`docs/plans/2026-09-28-restart-spec.md:207-211`).
  Why a hook instead of the manual ritual: the ritual fires only if the
  relaunched session remembers it; SessionStart fires because the harness
  runs it. Fallback when the pack tool is absent: inject the raw tails
  (card file + inbox tail), still capped, never a whole-file read (the
  40–70k whole-inbox cost measured in `docs/plans/2026-09-28-restart-spec.md:40-42`).
- PreCompact → card refresh before summarization (same card path; the
  `last-event` position in the header, `docs/plans/2026-09-28-restart-spec.md:140`,
  is what makes the next `--since-card` correct — compacting without
  refreshing orphans the position).
- Stop → same card write as exit-time safety net (covers sessions that end
  below the context cap; no git, no commit, one file rewrite per the
  one-writer rule, `.agents/skills/unattended-orchestration/references/state-file.md:3-7`).
- Out of P1: SubagentStop capture (P2, §2), SessionEnd lease release
  (forward-only, no fleet-node design here), Notification→question.json
  announcer (P2 — needs the ask-back contract `tools/autoos-ask.py:19-33`
  to stay the single state owner).

## 8. Migration/rollout

Hooks are additive. Today's manual D-080 practice keeps working unchanged:
a session that runs the ritual by hand and a session that gets the bundle
injected read the same pack, so mixed fleets cannot disagree. Rollout order:
(1) land the shared shape + validator (§4) with no renderer — catalog-only
change, covered automatically by the schema test convention (`AGENTS.md:143`);
(2) `claude-hooks` renderer + guard scripts, one machine, `--dry-run` first,
run twice (second run `skipped`, `AGENTS.md:113-124`); (3) opencode/OpenHands
renderers; (4) gemini/codex. No existing subcommand changes behavior; the
`check` subcommand (`lib/agent_harness.py:146-158`) validates the new keys
from day one, so a malformed guard fails loudly before any renderer runs.
Secrets rule applies throughout: guard patterns name shapes (`*.env*`),
never values (`AGENTS.md:16-21`).

## 9. Open questions

`docs/plans/2026-09-28-fleet-console-spec.md` section 12 does not exist in
this checkout (verified: `docs/plans/` holds no fleet-console spec) — using
`Q: HOOKS | question | options | default` per the brief's fallback.

- Q: HOOKS | Where do the rendered guard scripts live — `tools/hooks/` tracked in git, or generated at install time into the target dir? | tracked+generated | generated-only, single source stays JSON | tracked: reviewable, tested by the suite; generation keeps the JSON the only source either way. Decided in H1 (default: generated-only — the JSON stays the single source, scripts are render output like the opencode config).
- Q: HOOKS | PreToolUse push guard: reproduce the leaf/all split (orchestrator may push) or deny push for every Claude Code session in P1? | leaf/all split | deny-all | leaf/all split: matches the operator 2026-09-22 decision (`lib/agent_harness.py:59-61`); deny-all is simpler but breaks the merge lane.
- Q: HOOKS | SubagentStop capture in P1 or P2 — do worker exit writes hurt before the leaf contract references them? | P2 | P1 | P2: leaves already have an output contract; capture without a contract reader is write-only telemetry.
- Q: HOOKS | Notification→question.json announcer: which operator surface receives it (inbox line, human notify, both)? | inbox line | human notify | inbox line: keeps one state owner (`tools/autoos-ask.py`) and the existing beat already reads the inbox (`R-coord-08`).
- Q: HOOKS | opencode plugin vs plugin config — decided by reading the installed opencode's plugin API in the P-lane, or fixed now as config? | decide in P-lane | fix now | decide in P-lane: field names are unverified and guessing one is a defect.

## 10. Closing table

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| H1 | Shared shape + validator (`hooks`/`guards`/`startup_context` in `catalog/agent-harness.json`, `TOP_LEVEL_KEYS` + `validate()` extension, fence-name resolution test guard-vs-fences — names resolve, no copied pattern lists) + decides Q1 (default: generated-only) | small (final review only; free-model writer) | ~1 evening | nothing |
| H2 | Claude Code renderer + guard scripts (`.claude/settings.json` block, `claude-hooks` subcommand, SessionStart pack injection, PreToolUse ×3 guards, PreCompact/Stop card writes, `--dry-run` + double-run proof) | small–medium FINAL ONLY — flag: live harness tests may exceed ~1M weighted tokens if run per-matcher; keep to dry-run + one live session | ~2 evenings | H1 |
| H3 | opencode + OpenHands renderers (plugin/config after API verification; setup-script/microagent content; per-client "cannot express" headers; `--dry-run` + double-run `skipped` proof same as H2, `AGENTS.md:113-124` + `AGENTS.md:220`; owns the README update for its new subcommands, `AGENTS.md:222`) | small (final review only) | ~2 evenings | H1 (parallel with H2) |
| H4 | gemini/codex renderer (instructions-file + flags; thinnest output, mostly "no hook point" documentation; `--dry-run` + double-run `skipped` proof same as H2, `AGENTS.md:113-124` + `AGENTS.md:220`; owns the README update for its new subcommand, `AGENTS.md:222`) | small (final review only) | ~1 evening | H1 (parallel with H2/H3) |
