# fleetd + event bus — fleet API service: spec v1

Status: SPEC v1, unreviewed. Owner per brief: L2-general, gap spec 2/6 (routing-00).
Source architecture: the private routing plan §§2.2/2.3/10-phase-D, paraphrased below —
this file never quotes it verbatim and invents nothing beyond it.

Citation discipline: every claim about this checkout ends in a `path:line` citation
that resolves in this branch. Anything not verifiable in the checkout is marked
`(inference)`. Placeholders (`<management host>`, `<tailnet>`, `example.internal`)
are never real values — this repository is public (`AGENTS.md:16`).
Related specs (sibling branches, NOT in this checkout — cited by branch, file and
line, following the gap-spec-1 precedent for non-checkout sources): fleet-node
(branch `L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md`) owns the
per-host agent; this spec owns fleetd's side of the wire and reuses its §6 field
names (`register` / `launch` / `watchdog-report` / `kill`) without renaming.
The fleet console (branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md`) owns the UI surfaces fleetd serves
(kanban §6.1, steer/monitor feed §7.1/§7.3, push §7.4, question format §12).

Related work (not this spec): fleet-node internals (gap-spec 1, sibling branch above);
telemetry ingestion (gap-spec 3; fleetd is only a source for it).

## 1. Summary

fleetd is a thin API + MCP service **over** a bought event bus: `hosts`, `agents`,
`spawn` (placement → the AutoOS resolver → fleet-node), `send`/`inbox`, `events`,
question timers, digests. State lives in the bus's KV/SQLite with backup. This is
explicitly **"build, small"** (brief-sourced): fleetd is a coordinator, not where the
heavy lifting happens — per-host supervision is fleet-node's job (sibling branch
`L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md:15-43`), and
durability is the bus's job (§2).

The bus itself is **bought, not built**: NATS JetStream (a single binary, tailnet leaf
nodes) as the primary choice, Redis Streams as a fallback (both brief-sourced; neither
exists in this checkout — no NATS/Redis bus code is referenced anywhere in the
spawner, skill, or tests — so §2 picks between those two and designs nothing new).
fleetd stores references, not blobs: evidence lives in files in the repo/worktree,
linked from cards and events (inference on the link shape; the precedent is the run
record's file set — per-run dirs carrying `job.json` / `output.log` / `exit.json`,
`tools/autoos_agent_mcp.py:6-14`). Work items, questions, and decisions live in the
task board (Vibe Kanban — the console spec's §6.1 is not in this checkout, so this is
`(inference)`); `QUESTIONS.md`/`DECISIONS.md`-style files become **generated exports**
of the board once it is live, not fleetd's own data (inference). Operator surfaces
(this run's chat, a future console, the board, push) are out of scope for fleetd
itself — fleetd is the plumbing underneath, not the UI (brief-sourced).

fleetd's API is designed around exactly the §2.3 communication patterns
(brief-sourced, exhaustive — no more): any-agent-to-any-agent `send`; Claude-to-Claude
live fast path (below); the `fleet events --follow` wake path (§4); task-board
references; file-reference evidence. Nothing else gets an endpoint.

## 2. Bus choice

**Primary: NATS JetStream. Fallback: Redis Streams.** (Both names brief-sourced; the
reasoning below is `(inference)` except where cited.)

- *Why JetStream first (inference):* the brief's requirements — durable streams,
  per-consumer acks, replay, plus a KV store for the registry — are one product in
  JetStream (streams + consumers + KV), matching "a single binary" deployment on
  tailnet leaf nodes (brief-sourced). One binary keeps the per-host install story
  identical to fleet-node's idempotent install discipline (sibling branch
  `L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md:161-164`).
- *Why Redis Streams stays as fallback (inference):* if JetStream's single binary
  does not fit a host fleetd must serve (operator constraint, platform gap), Redis
  Streams covers durable streams + consumer groups + replay, with the registry KV
  mapped onto Redis hashes instead of JetStream KV. The fleetd API (§3) does not
  change between the two — only the storage adapter does.
- *Decision criterion (inference, proposed):* ship the JetStream adapter first;
  the Redis adapter is built only if a concrete host in the fleet cannot run the
  JetStream binary, or if the operator already runs Redis on `<management host>`
  and directs reuse. No second bus is designed — this section picks between the
  two named options and stops.
- *What fleetd assumes from either bus (inference):* at-least-once delivery with
  per-consumer cursors (the repo's own at-least-once read precedent: position-based
  resume returning every record past P in file order,
  `docs/plans/2026-09-28-restart-spec.md:39-44`); durable named consumers that
  survive reconnect; stream retention long enough for a digest window (§5).
  Subject/stream names, retention lengths, and shard boundaries are adapter
  configuration, not this spec (inference).

## 3. fleetd API + MCP surface

Field shapes, not REST prose. On the fleet-node side of the wire, the shapes are
the sibling spec's §6, reused verbatim (sibling branch `L2-general/fleetnode`,
`docs/plans/2026-09-28-fleet-node-spec.md:356-392`): `register` (`host_id`,
`platform`, `clients[]`, `free_ram_mb`, `total_ram_mb`, `max_units`,
`agent_version`), `launch` (`run_id`, `task`, `client`, `tier/model`,
`token_scope`) reporting `accepted | refused` with a `reason`, `watchdog-report`
(per-unit state/lease/fencing/resume-count/classifier/cpu/rss plus host
`free_ram_mb`), `kill` (`run_id | --all`, `reason`). fleetd is where these are
*received, stored, and acted on*; fleet-node is where they are *produced*.

- **`hosts`** — the registry fleet-node registers into. Rows come from `register`;
  re-registration upserts by `host_id`, never duplicates (sibling branch
  `L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md:414-415`).
  `clients[]` is the spawnable-client set the spawner already validates against
  (`tools/autoos_agent_mcp.py:6-14`). Shape (inference): the `register` fields
  plus `last_seen` (UTC ISO) and `threshold_overrides` fleetd may push back.
- **`agents`** — live + recently-exited units across hosts. Source rows are today's
  `ps --json` `{workers[], dir}` (`tools/autoos-agent.py:4084-4089`) extended with
  the `watchdog-report` lease/resource fields (sibling branch `L2-general/fleetnode`,
  `docs/plans/2026-09-28-fleet-node-spec.md:382-388`). Lifecycle vocabulary is the
  MCP/A2A set `submitted | working | input_required | completed | failed |
  canceled | rejected` (`tools/autoos_agent_mcp.py:84`), computed per run by
  `_state()` (`tools/autoos_agent_mcp.py:558`) — fleetd aggregates, it does not
  redefine states (inference).
- **`spawn`** — placement → the AutoOS resolver → fleet-node. fleetd picks the host
  (admission data from `hosts` + `agents`), mints the canonical run id (today:
  `mint_run_id`, `tools/autoos-agent.py:802-816`; a caller-handed id wins when one
  is given, `tools/autoos-agent.py:1846-1852`), and forwards a `launch` with the
  token scope (§8). Validation-before-side-effect is the established spawner
  contract — `spawn` validates (card → combo, depth budget, client rules) and
  returns a run id at once while the child runs detached
  (`tools/autoos_agent_mcp.py:6-14`); refusal (PAUSE gate exit 3,
  `tools/autoos-agent.py:4100-4112`; empty-task refusal; `refuse(reason, code)`
  convention, `tools/autoos-agent.py:2257-2259`) starts nothing and reports the
  reason, so "refused: memory" routes elsewhere rather than failing (inference).
- **`send` / `inbox`** — any agent to any agent (Claude or not, any host):
  `fleetd send` → bus → the receiver's `events` (at-least-once, ack, replay on
  reconnect — brief-sourced pattern; bus mechanics §2). The ask-back file shapes
  are reused, not reinvented: `question.json` `{"text", "asked"}` and `answer.json`
  `{"text", "answered"}` (`tools/autoos-ask.py:19-23`, file names
  `tools/autoos-ask.py:57-58`); a live run with an unanswered question is
  `input_required` and `respond` answers it, refusing any other state
  (`tools/autoos_agent_mcp.py:639-657`). Exclusive-create discipline for new
  questions mirrors `_create_json_exclusive` (`tools/autoos-ask.py:74-88`).
  Cursor shape for inbox reads follows the repo's existing position/record types
  (`tools/autoos_inbox.py:75-87`) and at-least-once resume
  (`docs/plans/2026-09-28-restart-spec.md:39-44`) (inference on the mapping).
- **`events`** — the durable stream (§2) behind `send`, steers, lifecycle
  transitions, watchdog crashes, question deadlines, and digest checkpoints.
  Consumers are durable named consumers with server-side cursors (§4); replay from
  any cursor is the catch-up primitive, digests (§5) are the human-scale one
  (inference).
- **Question timers** — §6. **Digests** — §5.
- **MCP surface** — fleetd's tools mirror the established MCP server shape:
  `FastMCP("autoos-agent")` with one function per verb
  (`tools/autoos_agent_mcp.py:684`), async spawn returning an id at once
  (`tools/autoos_agent_mcp.py:373`), `status`/`result` readers
  (`tools/autoos_agent_mcp.py:558-637`), `respond`/`cancel` writers
  (`tools/autoos_agent_mcp.py:639-677`). fleetd adds the same verbs at fleet scope
  (hosts/agents/send/events/digest/timer), same conventions (inference).

## 4. The wake path

This is the single biggest thing this spec changes about how runs operate: it
**replaces heartbeat crons** (brief-sourced acceptance: "no heartbeat crons left;
stale detection < 30 min").

- *Today's pattern (all cited):* L1/L2 orchestrators run a 10-minute
  `CronCreate`-scheduled heartbeat from launch to stop
  (`.agents/skills/unattended-orchestration/SKILL.md:107`); each beat pushes,
  pongs pings, WIP-commits past-beat work, stamps status, reads the inbox, and
  relaunches a child quiet >25 min from its handoff
  (`.agents/skills/unattended-orchestration/SKILL.md:108`). The heartbeat command
  itself is a read-only report — pause, repos, context, over-cap, exit code —
  that never pushes, commits, or writes (`tools/autoos-agent.py:2116-2124`); the
  PAUSE gate it feeds is `pause_state()` over the newest PAUSE/RESUME inbox lines
  (`tools/autoos_heartbeat.py:108-130`), ignoring pauses older than the session
  start (`tools/autoos_heartbeat.py:91-105`). The brief's claim of repeated
  `heartbeat - no new actionable items` lines in this run's own
  `inbox/L2-general.md` is brief-sourced (no `inbox/` directory exists in this
  checkout — checked — so it is cited by content, no checkout path, per the
  gap-spec-1 precedent).
- *The replacement (brief-sourced intent; console-spec shape from the sibling
  branch):* a woken-or-live Claude orchestrator runs **`fleet events --follow
  --session <id> [--last-event-id <ULID>]`** under Claude Code's `Monitor` tool,
  on a **durable consumer** (sibling branch `L2-general/fleetspec`,
  `docs/plans/2026-09-28-fleet-console-spec.md:500-513`). An event arrives, the
  follow emits one stdout line, `Monitor` turns it into a notification, and the
  session reads it at its **next tool round** — no wasted "nothing new" turns
  (sibling branch `L2-general/fleetspec`,
  `docs/plans/2026-09-28-fleet-console-spec.md:455-457`). Each orchestrator
  attaches its follow at launch, reading its own follow token from its own
  environment, so the follow is up before the first steer can arrive (sibling
  branch `L2-general/fleetspec`,
  `docs/plans/2026-09-28-fleet-console-spec.md:513`).
- *What makes an event worth waking for (inference):* a steer addressed to the
  session, an answer to its pending question, a `kill`/pause directive, a crash
  of a unit it spawned, or a question-timer expiry naming it. Everything else
  (lifecycle transitions of unrelated units, digest checkpoints, telemetry) is
  recorded but does not wake — the follow filters server-side by subscription,
  and the session filters client-side by the same wake-worthy set, so the two
  cannot drift (inference).
- *Durable-consumer/cursor mechanics (inference — no checkout pointer):* the brief
  suggests mirroring the OmniRoute Conductor bridge's cursor discipline, but the
  pointer it offers does not resolve: fleet-node-spec's full text (sibling branch
  `L2-general/fleetnode`, all 460 lines read) cites **no** `bridge.ts`, and no
  `bridge.ts` exists in this checkout (searched). So the mechanics are designed,
  not mirrored: each session owns one named durable consumer; the server holds
  the cursor (last acked event id); `--last-event-id` rewinds or fast-forwards it
  explicitly; reconnect resumes from the held cursor (at-least-once, mirroring
  the restart-spec resume contract, `docs/plans/2026-09-28-restart-spec.md:39-44`
  and the inbox position/record types, `tools/autoos_inbox.py:75-87`).
  A session that never attached a follow falls back to today's polling (inference;
  the console spec's fallback row is a sibling-branch claim).
- *What a woken session reads (inference):* not a full replay — it requests a
  digest (§5, `fleet digest --since <cursor>`), acks the events the digest covers,
  and only replays individual events the digest flags as needing full text.
- *What "stale detection < 30 min" means concretely (inference, anchored to
  `.agents/skills/unattended-orchestration/SKILL.md:108`):* the >25-minute quiet-child rule becomes a server-side check —
  any session whose consumer cursor has not advanced (no ack, no heartbeat-event)
  for 25 minutes is flagged `stale` in `agents` and surfaced to its parent and
  the operator; the 5-minute margin to the 30-minute acceptance is the detection
  loop's own period. The §10 acceptance probe: with all heartbeat crons deleted,
  a killed-but-unacked session is flagged stale within 30 minutes, and a healthy
  session never is.
- *Claude-to-Claude fast path (inference on the tool; evidence noted):* cross-
  session `SendMessage` is **not mentioned** in
  `.agents/skills/unattended-orchestration/SKILL.md` (searched — the only
  `SendMessage` evidence in the skill tree is a downstream report that
  cross-session messages to a `--remote-control` lane name worked,
  `.agents/skills/unattended-orchestration/references/PROPOSALS-2026-09-05-from-a-downstream-orchestrator.md:179-181`).
  The bus copy stays the record of truth; `SendMessage`, where available, is a
  latency optimization on top, never a replacement (brief-sourced rule).

## 5. Digests

A woken (or freshly-started) orchestrator needs a condensed catch-up, not a full
event replay. This is a summarization contract, not a new subsystem (inference).

- *Request (inference on the exact CLI shape, per brief):* `fleet digest --since
  <cursor>` returns everything the consumer has not acked since `<cursor>`,
  summarized; acking is separate and explicit, so a digest can be re-requested
  safely (safe-to-run-twice, `AGENTS.md:115`).
- *Contents (inference):* counts by event type since the cursor; anything
  requiring action first (unanswered questions addressed to the session, steers,
  kill/pause directives, crashes of owned units, expired timers) with one line
  each and their event ids; then a one-line-per-type rollup of the rest; finally
  the new cursor (the highest covered event id) to ack up to. Full event text is
  included only for the action-required items; everything else is counts plus
  pointers into replay.
- *Non-goals (inference):* no natural-language generation requirement in v1 — a
  deterministic template over the covered events satisfies the contract; a model-
  written summary may replace the template later without changing the request
  shape or the ack semantics.

## 6. Question timers

- *Today (all cited):* a worker's pending question is `question.json`
  `{"text", "asked"}` with no `answer.json`
  (`tools/autoos-ask.py:19-23`); **no per-question `deadline` field exists today**
  (searched `tools/autoos-ask.py` and `tools/autoos_agent_mcp.py` — the only
  `deadline` hits are the ask CLI's local poll timeout,
  `tools/autoos-ask.py:201-223`, and the v2 task card's routing `deadline`
  field, `tools/autoos-agent.py:33`, which governs deferral, not ask-back).
  On poll timeout the helper withdraws the question and archives a late answer
  as stale, never leaving `answer.json` behind
  (`tools/autoos-ask.py:218-230`); orphan answers are archived as `qa-<n>.json`
  with a stale marker, never silently deleted (`tools/autoos-ask.py:147-148`).
  The console schema already reserves the field this spec fills: a `questions`
  table with `deadline INTEGER` alongside `options`/`default_option`/`blocks`
  (sibling branch `L2-general/fleetspec`,
  `docs/plans/2026-09-28-fleet-console-spec.md:162`).
- *Design (inference):* fleetd records `deadline` (UTC epoch) at ask time from an
  explicit per-question TTL (default: configured, e.g. one beat period — exact
  value an open question, §10). The timer is server-side, so a sleeping asker's
  deadline still fires. At expiry fleetd writes a `question.expired` event and
  takes **exactly one** configured policy per question: `default-answer` (answer
  with the question's `default_option`, recorded as answered-by-timer, never as
  the human), `escalate` (re-address to a wider audience — parent session, then
  operator inbox — resetting the deadline), or `park` (leave `input_required`
  but flag stale for the digest's action-first section). Mixed policies per
  question class are configuration, not code branches (inference).
- *Timeout semantics (open, §10):* whether an expired-and-default-answered
  question may be re-asked by a human answer, and whether escalation chains have
  a depth cap, are operator decisions — the spec fixes the event trail either
  way: every expiry, default-answer, and escalation is an event, so the audit
  never depends on which policy was picked (inference).

## 7. State + backup

- *What lives only in bus streams (inference):* the `events` log itself —
  sends, steers, lifecycle transitions, watchdog crashes, expiries, digest
  checkpoints. Streams are the record of truth for "what happened"; nothing else
  needs to reconstruct it.
- *What fleetd itself persists in the bus KV/SQLite (inference):* the `hosts`
  registry (upsert by `host_id`), the `agents` rollup (rebuilt from
  `watchdog-report`s, but cached for reads), open `questions` with deadlines
  (§6), per-consumer cursors (§4), and the append-only audit log (§8). Process
  truth stays where it is today: per-run dirs with the spawner's canonical id
  (`tools/autoos_agent_mcp.py:6-14`), `ps --json` rows
  (`tools/autoos-agent.py:4084-4089`) — fleetd caches, it does not own, liveness.
- *Backup (inference):* the KV/SQLite store is backed up as a unit on a schedule
  (snapshot + event-stream retention covering at least two digest windows, so a
  restore never loses unacked events); restores are tested by replaying a backup
  into a scratch fleetd and diffing `hosts`/`agents`/`questions` against the live
  one. Exact cadence and retention are open (§10). No checkout precedent exists
  for this (no backup code in the spawner or skill) — marked inference throughout.

## 8. Security

fleetd does not redesign tokens — fleet-node mints, fleetd checks (brief-sourced
rule). The shapes are the sibling spec's §5, reused verbatim (sibling branch
`L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md:321-346`):
scope is exactly (`project`, `roles`, `spawn_quota`, `expiry`); the wire token is
an opaque random string with all scope fields held server-side so the holder
cannot edit scope; every call is checked against stored scope and out-of-scope
calls are rejected, not logged-and-allowed; leaf workers get `spawn_quota: 0`,
enforcing the leaf rule ("a leaf role never spawns",
`.agents/skills/unattended-orchestration/SKILL.md:133`) at the token layer.

- *Audit log shape (sibling §5 + checkout precedents):* append-only, one JSON
  object per line — the repo's established ledger pattern
  (`Write-HandoffLedgerEvent`,
  `.agents/skills/unattended-orchestration/HandoffCore.psm1:468-519`) — carrying
  at minimum `timestamp`, `token_id`, `actor`, `action`, `scope_at_check`,
  `decision` (`allow | deny`) (field list sibling-sourced; the JSON-lines
  discipline checkout-verified). Tokens are never written, only their ids, and
  key-name-matched plus bearer-shaped strings are redacted before persistence
  (`Redact-HandoffSecrets`,
  `.agents/skills/unattended-orchestration/HandoffCore.psm1:380-439`) (inference
  on the application to fleetd, precedent verified).
- *Session follow tokens (inference + sibling shape):* each session's `fleet
  events --follow` authenticates with a per-session token naming exactly one
  session id (sibling branch `L2-general/fleetspec`,
  `docs/plans/2026-09-28-fleet-console-spec.md:755`); a `--session` mismatch is
  refused. Follow tokens read one session's events and nothing else — strictly
  smaller than operator credentials (inference).
- *No shared secrets between hosts (inference, from sibling §5):* compromise of
  one host yields only its live session tokens, each expiring on its own; no
  long-lived inter-host credential exists to steal.

## 9. Migration

- *Cutover without a wake gap (inference, anchored to cited behavior):* today
  every orchestrator holds its 10-minute beat
  (`.agents/skills/unattended-orchestration/SKILL.md:107-108`). Migration is per
  session, never fleet-wide at once: (1) attach `fleet events --follow` alongside
  the still-running beat (observe-first — the follow's digest is diffed against
  what the beat's inbox read finds for at least one full beat period); (2) once
  the digest covers everything the beat found, delete that session's `CronCreate`
  beat; (3) the last beat deletion is what closes the "no heartbeat crons left"
  acceptance. At no point is a session unwatched: beat and follow overlap until
  the follow proves itself, and a session whose follow never attaches keeps its
  beat (inference).
- *Safe to run twice (`AGENTS.md:115`):* attaching a follow is idempotent —
  re-attaching the same named durable consumer resumes its held cursor, never
  duplicates delivery (inference, mirroring the at-least-once resume contract,
  `docs/plans/2026-09-28-restart-spec.md:39-44`); `fleet digest --since` is a
  pure read and re-requestable (§5); deleting an already-deleted beat reports
  stopped, not an error (inference). Rollback is forward-only: re-create the
  beat from the standing launch instructions, then detach the follow (inference).
- *Never clobber operator state (inference; `AGENTS.md:28-31`):* fleetd reads
  existing inbox/heartbeat config, adds its own stanza idempotently, and writes
  back; follow tokens and consumer names live in fleetd-owned config carrying no
  operator content.

## 10. Open questions

Format note: the real `Q: FLEETSPEC |` format below is taken from the console
spec's §12 (sibling branch `L2-general/fleetspec`,
`docs/plans/2026-09-28-fleet-console-spec.md:804-843`): `Q: FLEETSPEC | <topic> |
<question> | options: … | default: … | blocks: … | reversible: … |`, with an
`answer: (x)` suffix appended once decided. All questions below are open (no
`answer:`), all `(inference)` unless cited.

- `Q: FLEETSPEC | bus-final | Is NATS JetStream confirmed, or does a concrete host force Redis Streams now? | options: (a) JetStream, build Redis adapter only on a proven misfit (b) Redis now, JetStream never (c) both adapters up front | default: (a) because one binary on tailnet leaf nodes is the brief's primary for a reason | blocks: §2 adapter build, §7 backup shape | reversible: yes (fleetd API is bus-agnostic, §2) |` (inference.)
- `Q: FLEETSPEC | timeout-policy | At question-timer expiry: default-answer, escalate, or park — and what is the default TTL? | options: (a) default-answer with per-question default_option (b) escalate to parent then operator inbox (c) park as stale, digest surfaces it | default: (b) because silent auto-answers decide without a decider; TTL default one beat period | blocks: §6 policy config | reversible: yes (policy is per-question configuration) |` (inference; today's withdraw-and-archive precedent is `tools/autoos-ask.py:218-230`.)
- `Q: FLEETSPEC | digest-shape | Is the v1 digest a deterministic template or a model-written summary? | options: (a) deterministic template over covered events (b) model-written from the first day | default: (a) because the ack/cursor contract must be testable without a model in the loop | blocks: §5 implementation | reversible: yes (request shape and ack semantics do not change) |` (inference.)
- `Q: FLEETSPEC | wake-worthy-set | Which event types wake a sleeping session — the §4 five (steer, answer, kill/pause, owned-unit crash, timer expiry) or a wider set? | options: (a) the §4 five only (b) plus any event naming the session (c) everything, client-side filter only | default: (a) because waking is the scarce resource this spec rations | blocks: §4 server-side filter | reversible: yes |` (inference.)
- `Q: FLEETSPEC | stale-threshold | Is the stale flag at 25 min silence (5 min margin to the 30-min acceptance) or exactly 30? | options: (a) 25, margin for the detection loop (b) 30 exactly | default: (a) because it inherits the proven >25-min quiet-child rule (`.agents/skills/unattended-orchestration/SKILL.md:108`) with room for the loop period | blocks: §4 acceptance probe | reversible: yes |` (precedent cited; values inference.)
- `Q: FLEETSPEC | backup-cadence | How often is the KV/SQLite store snapshotted and how long are streams retained? | options: (a) hourly snapshot, 7-day stream retention (b) daily snapshot, 30-day retention (c) on every digest checkpoint plus daily | default: (a) as a starting bid only | blocks: §7 backup implementation | reversible: yes |` (inference; no checkout precedent.)
- `Q: FLEETSPEC | token-lifetime | How short is short-lived for fleetd-checked capability tokens? | options: (1) unit lifetime, expiry = lease end (2) fixed hours with renewal (3) per-call | default: (1) because it ties compromise window to the supervised unit | blocks: §8 enforcement | reversible: yes |` (inference; same question is open on the sibling spec's side.)

## 11. Closing table — lanes + estimated Claude tokens

Budget mode (per brief: writers/first-reviewers are free models, Claude only
reviews/finals) — every row's Claude estimate stays small (under 1M weighted
tokens each), same shape as the sibling spec's §9 (sibling branch
`L2-general/fleetnode`, `docs/plans/2026-09-28-fleet-node-spec.md:448-460`).
No lane below looks larger than that; none is flagged (inference: sizing
judgement, not measurement).

| Lane | What | Claude est. | Wall time est. | Waits for |
|---|---|---|---|---|
| fleetd-bus-adapter | §2 JetStream adapter (streams + durable consumers + KV) behind the bus-agnostic fleetd API; at-least-once resume per the restart-spec contract (`docs/plans/2026-09-28-restart-spec.md:39-44`); snapshot/restore probe per §7 | ~250k | 2–4 days | §10 bus-final answer; a `<tailnet>` test host |
| fleetd-api | §3 hosts/agents/spawn/send/inbox/events + MCP surface mirroring the established server shape (`tools/autoos_agent_mcp.py:684`); wire compat with the sibling §6 field names; `refuse(reason, code)` convention (`tools/autoos-agent.py:2257-2259`) for route-elsewhere refusals | ~400k | 3–5 days | fleet-node wire (sibling branch) for live compat, stub-able until then |
| fleetd-wake-digest | §4 follow CLI + durable-consumer/cursor mechanics + wake-worthy filter + `Monitor` launch wiring; §5 digest contract and template; stale-flag loop with the <30-min acceptance probe | ~300k | 3–4 days | fleetd-api (events substrate); heartbeat-beat behavior as oracle (`.agents/skills/unattended-orchestration/SKILL.md:107-108`) |
| fleetd-timers | §6 deadline field on the ask-back shapes (`tools/autoos-ask.py:19-23`), server-side expiry, one policy per question (default-answer/escalate/park), expiry/escalation events; §10 timeout-policy answer | ~150k | 1–2 days | fleetd-api (questions store); §10 timeout-policy answer |
| fleetd-cutover | §9 per-session beat→follow migration with overlap-then-delete, rollback path, idempotency per `AGENTS.md:115`; final sweep proving no heartbeat crons left | ~150k | 2–3 days | fleetd-wake-digest (proven follow); live orchestrators to migrate |
