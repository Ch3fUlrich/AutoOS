# RESTART — cheap relaunches: state card + context pack (spec v3.3)

Owner: autoos-L1-routing. Operator decisions D-040 (restart) and D-042 (provenance), 2026-09-28,
relayed by the L0 router.
Status: SPEC v3 (v2 Sonnet FIX-FIRST resolved, table under Delivery). v1 (0fbed20, c16ac78) got a cross-family first pass (Qwen,
`work/L1-routing/review-restart-spec.out`: 7 blockers, 11 high); v2 resolves every finding (§R at
the end). Next: the lanes (v3.3's Sonnet FIX-FIRST is resolved, table under Delivery).
v3.2 (RSTAMEND, 2026-09-28): new §7 size limits, crash recovery through the pack (§4), the L2 cap
decision (§5), and two delivery lanes (R2c, R8).
v3.3 (RSTAMEND2, 2026-09-28, Sonnet FIX-FIRST on 86da423): §7 rotate now archives only what a
covering `→ done:` names, and only under the new §0 inbox lock; §7 stops calling `status/<name>.md`
the card (§1 owns that path); §4 liveness points at the §3 snapshot; §5 takes the L2 cap to 250k
(router D-085) and new §8 attacks the fresh-session baseline behind it (lane R9).

## Why (measured)

- A relaunch reads 14–17k tokens when it reads only the inbox tail, and 40–70k when it re-reads
  whole inboxes (L0, 2026-09-28). The largest inbox is `inbox/L1-routing.md`: 214,669 B ≈ 53.7k
  tokens. An L2-only inbox is ~7k.
- 20+ orchestrator relaunches in 48 h.
- On this host, 99.2 % of the orchestrator tokens in 48 h were cache reads (4.45 B of 4.48 B,
  16,138 turns). So the per-turn cost grows with context size: a turn at 600k re-reads 600k.
  The byte-identical prefix pays off *within* a session (every later turn reads it from cache). A
  successor's first request pays the full input once, and that is what the ≤8k variable part keeps small.
- What a relaunch actually reads, measured on a crash-recovery relaunch (routing-00 12:3xZ; L1-main
  from L1-backlog session 8e409b42's transcript): the L2 policy cap then was 150k (§5 carries the
  current number), and the relaunch reached 153.9k after 28 tool results / 143k chars. About 70k of
  that were whole-file reads — inbox 52k chars, handoff 19k, status 17k, `briefs/common.md` 9.5k,
  three log dumps 9–11k each. L1-routing's own crash relaunch (12:1xZ) read the same kind of files
  whole. §7 puts a hard budget on each of them.

## Goal

A relaunched L1/L2 orchestrator reads one generated pack file: a capped, byte-stable prefix plus at
most 8k variable tokens, estimated conservatively. It never opens a whole inbox. The pack is built by
code from state the session keeps small at every wave. Then the context cap drops to ~350k.

## Non-goals

- The console/event store that replaces inbox markdown (later; the inbox stays the archive).
- Memory recall itself: the pack reserves a slot with a stub until the memory spec lands.
- Worker (L3) relaunch: workers are relaunched from their brief (R-orch-06), not from a card.

## 0. Shared definitions (one home: `tools/autoos_inbox.py`, new)

- **RUN dir:** `$AUTOOS_RUN_DIR`. The tools never guess it. An inbox is `<RUN>/inbox/<name>.md`,
  or an explicit `--file PATH`.
- **Record:** one line that starts with an ISO UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`), plus every
  following line without one (continuations). A leading untimestamped block, such as the `# inbox …`
  header, is not a record and is skipped.
- **Position:** `<timestamp>#<ordinal>`, where the ordinal is the record's 1-based index among the
  records with that exact timestamp, in file order. It is unique even when records share a second:
  measured, 81 same-second collisions in one inbox. Reading "since P" is at-least-once: it returns
  every record whose (timestamp, ordinal) is > P, in file order.
- **Out-of-order timestamps:** reading uses file order; a record whose timestamp is lower than a
  record before it is still returned when it is after P in file order, and is flagged `(late)`.
- **No parseable timestamp:** an inbox with records but no timestamp (another shape) makes every
  reader exit 1 with `no timestamped records in <file>`. It never reads as "no events".
- **Acknowledgement markers:** one list, `tools/autoos_heartbeat.py` `_NOT_AN_ORDER_RE`. It holds
  only `lesson:|→ done` today. Lane R2a extends it to the markers in use (`→ done`, `→ ack`,
  `→ relaunched`, `→ operator`, `→ main`) plus the other line shapes that record the past rather than
  ask for the future — a `ready` line, a pong, and an `info` line — with a test per shape. It is the
  ONE "is this an order" answer, and each reader says which part of it it uses: §1 takes the subset
  that closes something for staleness, §3 cites it for readies, §7 takes the whole list for
  archivability.
- **Inbox lock:** every write and every rotation of `<RUN>/inbox/<name>.md` takes `flock` on
  `<RUN>/inbox/<name>.lock` — one lock, two modes. `append_inbox_line` holds it **shared** and only
  for the write (several appenders may hold it at once; the file is opened `a`, one complete line each).
  A rotation holds it **exclusive** for its whole read-write-swap, so it waits for every appender and
  no append can start inside the window. Nobody renames or truncates an inbox without holding it.
  §7's rotate cites this; the lock is not defined twice.
- **Concurrent writers:** several sessions append to one inbox. A reader ignores a final line that
  has no trailing newline (a torn append): it is not a record and not a continuation, and the next
  read sees it whole. Writers append one complete line per write (`append_inbox_line` already does,
  in `a` mode). A record whose timestamp line fails the ISO parse *after* a newline is reported as
  `malformed at line N`, never glued onto the previous record. What keeps an append and a rotation
  from racing each other is the lock above; the torn-line rule only describes what a reader sees.

## 1. State card — `<RUN>/status/<name>.card.md`

One per session, written only by that session (one writer per file). Rollover: the card lives in
the run dir. A new run starts a new card, which is seeded from the old one's `goal`, `threads` and `traps`.

- At most 40 lines. Fixed sections in this order, each required, each within its line cap:

| section | max lines | content |
|---|---|---|
| header | 1 | `# card <name> — <UTC> \| gen=<manifest-id> \| context <n>k/<cap>k \| last-event <position>` |
| goal | 3 | the standing goal, who set it |
| state | 6 | branch shas, main sha, what is held and why |
| next | 3 | the next three actions, in order |
| threads | 12 | one line per open lane/question/ready: `<id> \| <where> \| <state> \| <next>` |
| traps | 8 | things a successor would get wrong, each with an evidence pointer |
| operator | 4 | operator-only steps, verbatim |

- Every line is at most 200 characters (measured: today's status files break this, 31 of 105 lines
  in one). So a successor writes a fresh card and does not convert the old status file.
- The card is updated every wave with a small edit. R-coord-06 changes from "at cap: rewrite
  state" to "at cap: `card check`, `pack`, hand off". The card is already current (R6 rewrites the rule
  and `references/state-file.md`).
- `autoos-agent.py card check <file>` checks the section order, the line caps, the header fields, that
  `last-event` parses as a position, and the 200-char limit. It prints each problem with its line
  number. Exit 0 ok, 1 invalid, 2 unreadable.
- Heartbeat adds `card: stale` when a record after `last-event` carries an acknowledgement marker
  (§0) written by this session: the session acted on something its card does not absorb. Of the §0
  list it reads only the markers that *close* something (`→ done`, `→ ack`, `→ relaunched`,
  `→ operator`, `→ main`); a `ready`, pong or `info` line is not an order but does not make a card
  stale, or every 10-minute beat would. That needs
  `heartbeat_state` (autoos-agent.py), the JSON key tuple of `cmd_heartbeat --json`, and the MCP twin
  (`tools/autoos_agent_mcp.py` `_heartbeat`/`heartbeat_info`) to gain a `card` parameter and key (lane R2).

## 2. Inbox reads — `autoos-agent.py inbox`

- `inbox <name>|--file PATH` takes `--since-card <card>`, `--since <position|UTC>` or `--all`. It
  also takes `--max-records N` (default 30), which cuts the OLDEST and prints `cut N earlier records`.
- The records are printed whole, with their continuations. Without `--since*` or `--all`, it
  refuses (exit 2). Relaunch prompts and the skill use `--since-card` only.

## 3. Context pack — `autoos-agent.py pack <name>`

The pack is written to a file (`--out`, default `<RUN>/status/<name>.pack.md`), and its path is
printed. The order is:

1. **Prefix (byte-stable for a role):** the SKILL.md rules section plus
   `references/main-orchestrator.md`. There are no timestamps or shas inside it, and it is capped at
   24 KB (`--prefix-cap`). The pack reports the prefix sha256 and its per-turn cache cost
   (prefix tokens × 0.1). The run-specific role brief `<RUN>/briefs/<name>.md` is NOT in the prefix;
   it is variable, since it changes per run.
2. **Snapshot (code-measured):** the `l1_handoff.py` snapshot (repo, main/origin, sessions, helpers,
   and its `lanes()`, the ONE lane source); live workers; open readies; open questions.
   - **Live workers** is the only place a successor learns what is running: `ps` for spawned workers,
     plus `systemctl --user` for the heartbeat units, both through `heartbeat`. It is a snapshot line,
     so no relaunch prompt restates it (§4).
   - An **open ready** is a `ready <branch> <sha>` record this session wrote into its parent's inbox
     for which no later record in its own inbox from the parent contains the first 7 hex of `<sha>`
     together with `main=`.
   - **Open questions** are NOT inferred from inbox text. Measured: real answers are free text
     ("Q-008 (a) -> REDACTMERGE queued", "answers Q-001/Q-003"), and mentions of an id are not
     questions, so no pattern classifies them (Sonnet v3 review). The source of truth is the card:
     every open question is a `threads` line whose id starts with `Q` (`Q-008 | routing-00 | asked
     22:33Z | default a`). The session closes it by deleting the line when the answer arrives. The
     pack prints those lines under the snapshot's `open questions` heading, and `card check` rejects a
     `Q` thread without an `asked <time>` field.
3. **Memory:** stub `memory: not wired (MEMSPEC)`.
4. **Role brief:** `<RUN>/briefs/<name>.md`, verbatim.
5. **State card:** verbatim.
6. **Events since the card:** `inbox --since-card`, with `--max-records 30` and its own sub-budget
   of 4k tokens (older records are cut first).

- **Budget:** "variable" means the exact bytes of parts 2–6 as written to the file, headers
  included. The estimate is bytes ÷ 3, rounded up. That is conservative against dense sha/pipe/ISO
  text: measured prose runs at about 4, so ÷3 over-estimates. When variable > `--budget` (default
  8000), it exits 1 with the estimate. Measured today: ~5.1k typical, ~9k worst case, so the 30-record
  default and the 4k events sub-budget are what keep the worst case under budget.
- `l1_handoff.py --pack` makes the handoff file the pack.

## 4. Relaunch command — `autoos-agent.py relaunch-line <name>`

- The facts come from `<RUN>/run.json` (git-ignored, per run): for each session its name, worktree,
  model, MCP config path and parent. The tracked example is `configuration/run.example.json`, with
  placeholders only (AGENTS.md rule 1). `.claude/handoff.config.json` is a different tool's config
  and is not used.
- It prints one command whose prompt is a single line: `Read <pack path> first; it is your
  context.` The pack itself never goes into argv (quoting, size). It prints the command; running it
  is the parent's action.
- **Crash recovery uses the same path.** `relaunch-line <name> --reason crash` builds the same pack
  (§6 manifest `reason=crash`) and prints the same one-line prompt. The relaunching parent adds
  nothing but the pack path — no prose summary of what it saw.
- Worker and unit liveness is a snapshot line (§3 item 2), never a prompt line: the pack already
  carries what is running, so `relaunch-line` adds nothing but the pack path.

## 5. Context cap and the metric

- **Cap:** per orchestrator role, not per model (router D-044): the shared registry row
  `claude-opus-1m` (`["opus","fable"]`) goes to `cap_tokens` 350000 / `cap_fraction` 0.35 (source
  D-040, D-044). If Fable's before/after numbers show a quality loss, the row is split then. `tools/autoos_context.py` `DEFAULT_CAPS` (the unreadable-registry fallback) and its
  pinned tests change in the same lane. `tools/registry.py validate` asserts
  `cap_tokens == window × cap_fraction`.
- **L2 cap (router L1-routing D-085, 2026-09-28): 250k.** v3.2 held L2 at 150k and named its revisit
  trigger; the trigger is measured and met. L1-backlog hands off every ~15 min, and a fresh sonnet L2
  baseline is **55.5k** — system prompt + MCP tools + CLAUDE.md, read off the first turn of session
  967be39d. At 150k that leaves ~95k of work per session, so the handoffs, not the work, were what
  cost. The number lives in the registry, never in prose: a new `claude-sonnet-1m` `handoff_caps` row
  (sonnet, `window` 1000000, `cap_tokens` 250000 / `cap_fraction` 0.25 — the invariant
  `tools/registry.py validate` asserts). Lane CAPL2. §8 goes after the 55.5k itself; lower this row
  again once R3/R4 land and a measured relaunch costs < ~20k.
- **Metric: `autoos-agent.py token-rate --since <ts> [--until <ts>]`.** This is a new verb; `usage`
  stays gateway-only, so the metric needs no gateway.
  - Its numerator is the orchestrator sessions' weighted tokens: every usage record in the Claude
    Code transcripts under this repo's lanes, summing input + output + cache_creation + 0.1 ×
    cache_read. A new iterator reads all records; `fill_from_transcript` only keeps the last one.
  - Its denominator is the first-parent merges into `main` whose message names this orchestrator's
    branch prefix (`Merge L1-routing/…`, `Merge l1/routing …`), so numerator and denominator are the same actor.
  - It also reports the naive unweighted sum and, when the gateway is reachable, the workers' gateway
    tokens by lane tag, both as labelled diagnostics.
  - Known bias, stated in the output: it rewards shorter sessions, and it prices the orchestrator only
    (the workers are a diagnostic).
- **Before:** the 48 h up to the cap change. **After:** the 48 h after it. Both go to the L0 router,
  with windows and counts.

## 6. Context provenance (operator D-042, PLAN §16)

Goal: lineage for every context a session starts from. The console can show which pack a session
started from, diff two packs, and answer "why this decision" by replaying the exact pack. Trade-off
(operator retention D-042): exact replay works for 90 days. After that the manifest still names every
part by sha, which proves lineage but can no longer rebuild the bytes.

- **Redaction:** the pack FILE and every blob are written through the repo's one secret-pattern module
  (the session reads the redacted pack, so `--replay` rebuilds exactly what it read);
  (`tools/autoos_redact.py`, landing with REDACTMERGE). Packs quote inbox text, briefs and operator
  lines verbatim, so a blob holds the redacted text and its sha is the sha of the redacted bytes.
  R7 starts after REDACTMERGE is on main.
- **Canonical JSON:** manifests are serialized with `json.dumps(obj, sort_keys=True,
  separators=(",", ":"), ensure_ascii=False)`, UTF-8, integers only (token counts), and UTC times as
  `YYYY-MM-DDTHH:MM:SSZ` strings. A test pins the id of a fixed manifest.

- **Store:** `logs/context/` in the repo the session runs from (git-ignored, like `logs/sandboxes/`).
  The module is `tools/autoos_context_store.py` (new).
  - `blobs/<sha256>`: each pack part is stored once, content-addressed. The prefix, snapshot, memory,
    brief, card and events are separate blobs.
  - `manifests/<manifest-id>.json`, where manifest-id = the first 16 hex of the sha256 of the canonical manifest JSON.
  - `events.jsonl` is append-only, with one `{"type": ..., ...manifest}` line per pack, spawn or
    card version. It stands in for the console's event stream until that exists.
- **Manifest fields** (all required; absent ones are explicit `null`):
  - `type` (`context_pack` | `spawn` | `card`), `session` (name + Claude session id when known);
  - `generation` (the previous manifest of that name, +1), `parent` (that manifest id);
  - `reason` (`cap` | `clear` | `crash` | `operator` | `first`);
  - `parts` {name → blob sha} and `prefix_version` (= prefix sha);
  - `memory` {recall ids + versions; stub `[]`}, `events` {first position, last position, count};
  - `tokens` {prefix, variable}, `created` UTC.
- **Carry-through:** the pack's first line is `gen=<manifest-id>`, and the session copies it into its
  card header. `ready` appends ` gen=<id>` from the card's header when the card is given (`--card`).
  Question and decision lines written by hand carry it by rule (R6).
- **Spawn briefs:** `autoos-agent.py run` stores the brief blob plus a `spawn` manifest with the brief
  sha, the resolver's choice (route, leg, client, model, reason), the parent session + gen, and the
  sandbox branch. The run prints `gen=<id>` in its header, and the lane record cites it.
- **Card versions:** every `card check` that passes stores the card blob and appends a `card` event
  (sha, previous sha). A successor can diff any two waves.
- **Retention:** `autoos-agent.py context-store prune` deletes blobs older than 90 days that no
  manifest from the last 90 days references. Manifests and `events.jsonl` are kept forever. Heartbeat
  runs the prune at most once a day.
- **Replay/diff:** `pack --replay <manifest-id>` reassembles the exact bytes from the blobs. It exits 1
  naming a pruned blob that is missing. `pack --diff <id1> <id2>` diffs two packs part by part.

## 7. Size limits on every file a successor may open (hard, checked by code)

§Why: a relaunch blew its cap reading whole files. Every file a successor may open has a byte budget,
and the writer that produces it enforces the budget. A limit no code checks is prose.

- **Card:** the §1 cap, checked by `card check`.
- **Pack:** the §3 variable budget, checked by `pack`.
- **Handoff file** `<RUN>/status/<name>.handoff.md`, written by `l1_handoff.py`: at most 5k tokens,
  estimated as §3 estimates. Over budget it exits 2 and names the section that is over budget; it never
  writes a file bigger than the budget. It never repeats a line — today it prints the same
  `session … not running` line 5 times — and it never embeds the status file whole
  (`render(snap, state_text)` does today); it points to the card. Lane R2c.
- **Status file** `<RUN>/status/<name>.md`: the old free-form status file. It is **not** the card —
  the card is `<RUN>/status/<name>.card.md` (§1), and that path is fixed there. `status/<name>.md` is
  retired once R6 lands, because heartbeat and `l1_handoff.py` read the card and a second state file
  would be a second home for the same facts. Until R6 merges, it still exists and is capped by the
  same §1 budget — at most 40 lines, at most 200 characters a line — and `card check` takes a path and
  runs on whichever of the two files exists, so the cap is code-checked, not prose (lane R2a; R6
  deletes the file and the second call site with it).
- **Inbox rotation:** new verb `autoos-agent.py inbox rotate <name>|--file PATH`.
  - Trigger: the inbox file exceeds 50 KB, or its first record is from an earlier UTC month than today.
  - **Archivable** — this is what "acknowledged" means, and position alone is not enough of it. A
    record moves only if either: (a) it is **not an order**, by the one §0 `_NOT_AN_ORDER_RE` list
    R2a extends (`lesson:`, `→ done:`, a `ready`, a pong, an `info` line) — such a line records what
    already happened, so nothing downstream waits on it; or (b) it **is** an order and a later
    `→ done:` line **names it** by that record's own leading UTC timestamp — the
    `→ done: 12:12:24 <what shipped>` form real inboxes use, and one done line may name several
    timestamps. Anything else is an open order and stays live **whatever its position**. (why: with
    position as the only test, an unrelated `→ done:` that happened to follow a PAUSE archived the
    PAUSE and the successor never saw the order again.)
  - Moves: every archivable record at or before the card's `last-event` position (§0), into
    `<RUN>/inbox/archive/<name>-<YYYYMM>.md`, filed under the record's own month.
  - Stays: everything after the card's position, every record that is not archivable, and the
    `# inbox …` header. Rotate prints `kept N records: no covering done` — N counts the orders it
    refused to archive because no later `→ done:` named them.
  - Swap, under the §0 inbox lock held **exclusive** for the whole operation: re-read the live file
    under the lock (never decide from a read taken before it), append the moved records to the
    archive, write the new live file to a tmp in the same directory, `fsync` both, rename the tmp over
    the inbox, release. The §0 append rules hold across the swap: a torn last line stays a torn last
    line and is never glued onto a record, and an append cannot be swallowed by the rename because an
    appender holds the shared lock the rotation waits for.
  - Idempotent: a second rotate moves nothing.
  - `heartbeat` runs it at most once per hour per inbox.
  - Reads: `inbox --all` reads the archive then the live file; `--since-card` never opens the archive.
  Lane R8.
- **Prompts and skill rules:** no relaunch prompt and no skill rule tells a session to read an inbox,
  handoff, status, brief or log whole. A log is read with `tail -n 40` or a grep. The only prompt a
  relaunch gets is §4's one line; R6 writes the rule.

## 8. Fresh-session baseline (router D-085, 2026-09-28)

§5 raised the L2 cap to 250k to *ride out* the baseline. The baseline is what a fresh session pays
before it reads anything of its own: measured 55.5k for sonnet L2 = system prompt + MCP tool schemas +
CLAUDE.md. The system prompt is not ours; the other two are, and both are cut per role, not per model.

- **A lean MCP set per role.** Each role gets a strict `--mcp-config` file that lists *only* the
  servers that role uses. Tracked as `.example` templates with obviously fake values, like §4's
  `run.example.json` — a real one would carry machine paths AGENTS.md rule 1 forbids here. Measured
  intent: L2 orchestrator = `autoos-agent` + `omnigraph`; worker = nothing beyond its client's own
  defaults; reviewer = none. A role that must not spawn lists no `autoos-agent` server at all, so the
  tools are not merely unused but absent (R-worker-06). `relaunch-line` and `run` pass the path §4's
  `<RUN>/run.json` already holds per session — that field is the only place a role's config path is
  written down.
- **A trimmed always-loaded set.** CLAUDE.md / AGENTS.md keeps only what *every* session must read
  (the hard rules, the entry points, the definition of done); everything else moves behind a pointer
  to the skill or reference file that owns it. One home per fact decides what stays: a fact whose home
  is elsewhere is a pointer there, not a copy here.
- **Measure it before and after, per role.** The baseline is one fresh session's first-turn input
  tokens, read from the transcript's usage — the same all-records iterator §5's `token-rate` uses, so
  it needs no gateway. `tools/autoos_tokenrate.py` gains a `baseline` measurement and
  `autoos-agent.py token-rate --baseline [--role <name>]` prints it per role. Both numbers — before,
  after — go to the L0 router with the role named. Lane R9.

## Delivery (each lane test-first; cheap writer → cross-family review → Sonnet final)

| lane | scope | files (one writer each) |
|---|---|---|
| R1 inbox | §0 module + `inbox`; the §0 inbox lock on the append side (`append_inbox_line`, shared); replace `main()`'s dispatch fallthrough (`cmd_list … else cmd_run`) with an explicit verb table so later verbs cannot fall into `run` | tools/autoos_inbox.py (new), tools/autoos-agent.py (dispatch + inbox verb), tests |
| R2a card | `card check` + extend `_NOT_AN_ORDER_RE` (the ready/pong/info shapes too, §7); `card check` takes a path so it also caps `status/<name>.md` until R6 retires it | tools/autoos-agent.py, tools/autoos_heartbeat.py, tests |
| R2b stale | heartbeat `card: stale`: `heartbeat_state` param, JSON key tuple, MCP twin | tools/autoos-agent.py, tools/autoos_agent_mcp.py, tests |
| R2c handoff cap | §7 `l1_handoff.py` budget (refuse over 5k, name the section), line dedupe, no embedded status file | .agents/skills/unattended-orchestration/l1_handoff.py, tests |
| R8 rotate | §7 `inbox rotate` verb: archivable = non-order (§0 list) or named by a later `→ done:` timestamp, `kept N: no covering done`, exclusive §0 lock + re-read, tmp/`fsync`/rename swap, heartbeat hook (≤1/hour/inbox), archive read rules | tools/autoos_inbox.py, tools/autoos-agent.py, tests |
| R3 pack | `pack`, budget, `l1_handoff.py --pack` | tools/autoos-agent.py, .agents/skills/unattended-orchestration/l1_handoff.py, tests |
| R7 provenance | §6 store, manifests, events, `gen=`, spawn manifests, card versions, prune, replay/diff | tools/autoos_context_store.py (new), tools/autoos-agent.py, tests |
| R4 relaunch | `relaunch-line`, run.json schema + example | tools/autoos-agent.py, configuration/run.example.json (new), tests |
| R5a metric | `token-rate` verb + all-records iterator; the before-number | tools/autoos_tokenrate.py (new), tools/autoos-agent.py, tests |
| R5b cap | shared opus/fable row 350k (D-044), DEFAULT_CAPS + pinned tests, validate invariant | catalog/ai-registry.json, tools/autoos_context.py, tools/registry.py, tests |
| CAPL2 | §5 new `claude-sonnet-1m` handoff_caps row 250k/0.25 (D-085) + DEFAULT_CAPS fallback | catalog/ai-registry.json, tools/autoos_context.py, tests |
| R6 skill | R-coord-06/08 text; `references/state-file.md` becomes the card spec (its only writer); the stale `briefs/common.md` rule sources | SKILL.md, references/ |
| R9 baseline | §8 per-role strict `--mcp-config` files + the flags that pass them, the always-loaded trim, the `baseline` measurement | configuration/mcp/ — one `<role>.json.example` per role (new), tools/autoos-agent.py (relaunch-line, run), tools/autoos_tokenrate.py, AGENTS.md, CLAUDE.md, tests |

Order: R1 first (it freezes the dispatch). Then R2a and R5a in parallel (different files except one
dispatch-table line each), then R2b. Then R2c and R8, right after R2b and before R3. Then R3, R7 (after
REDACTMERGE is on main), R4, R5b (after the before-number), CAPL2 (after R5b — the same registry and
fallback files, one writer at a time), R9 (after R4, since it adds a flag to `relaunch-line`, and after
R5a, since it extends the token-rate module), and R6. Every lane after R1 merges main
before it starts. Each lane is at most three items per worker run.

| v2 Sonnet finding | v3 resolution |
|---|---|
| blobs store raw inbox/brief text | §6 Redaction through autoos_redact; R7 after REDACTMERGE |
| opus row also matches fable | §5 router D-044: cap is per role, row stays shared, both 350k |
| `_NOT_AN_ORDER_RE` is only `lesson:\|→ done` | §0 R2a extends it, test per marker |
| torn appends by concurrent writers | §0 last line without newline ignored; malformed reported |
| R2/R5 over 3 items | R2a/R2b, R5a/R5b |
| canonical JSON undefined | §6 pinned serialization + id test |
| 90-day prune vs replay | §6 trade-off stated in the goal |
| "answered" left to prose | v3.1: open questions live in the card's `threads` (Q-lines), not inferred from inbox text (v3 re-check: patterns unattested) |
| pack vs blob redaction | v3.1: the pack file itself is redacted; replay is exact |

| v3.2 Sonnet finding (on 86da423) | v3.3 resolution |
|---|---|
| HIGH: rotate's "acknowledged" undefined; position alone archives an open PAUSE/order when an unrelated `→ done:` follows | §7 defines archivable — non-order by the §0 list, or an order a later `→ done:` names by its leading UTC timestamp; everything else stays live whatever its position, and rotate prints the kept count |
| HIGH: rotate's read-to-rename window drops a concurrent §0 append | §0 inbox lock (`<inbox>.lock`): shared and short on every append, exclusive across rotate's re-read → tmp → `fsync` → rename; §7 points at it |
| HIGH: §7 called `status/<name>.md` "the card" while §1 fixes the card at `status/<name>.card.md` | §7 keeps one path (§1's) and says the free-form file is retired when R6 lands; until then it carries the same 40-line cap |
| MEDIUM: §4 sent liveness to §3 item 6 and carried its sources there | Liveness is the §3 item 2 snapshot line, sources named once there; §4 only says it never goes into a prompt |
| L2 cap 150k vs the measured trigger | §5 D-085: trigger met (handoff every ~15 min, 55.5k fresh sonnet baseline), new `claude-sonnet-1m` row at 250k, lane CAPL2 |
| what the cap was riding out | new §8: lean per-role `--mcp-config` + trimmed always-loaded set, measured before/after per role, lane R9 |

## §R. v1 review findings → v2 resolution

| finding | resolution |
|---|---|
| last-event not unique (81 collisions) | §0 position = timestamp#ordinal, at-least-once |
| `inbox <name>` has no name→path | §0 `$AUTOOS_RUN_DIR` or `--file` |
| `usage` is gateway-only | §5 new verb `token-rate` |
| `fill_from_transcript` keeps one record | §5 new iterator over all records |
| no role brief file in the skill | §3 prefix = rules + main-orchestrator.md; run brief is variable |
| `.claude/handoff.config.json` is tracked and stale | §4 git-ignored `<RUN>/run.json` + tracked example |
| open readies unmatched | §3 pair on sha prefix + `main=` in the parent's answer |
| cache weight open | §5 weighted headline, naive as diagnostic |
| metric measures context, not work; actors differ | §5 same-actor denominator, bias stated |
| worker cost invisible | §5 gateway lane tokens as diagnostic |
| DEFAULT_CAPS hardcodes 600k | §5 changes it + tests; validate asserts invariant |
| prefix excluded from budget | §3 prefix cap 24 KB + per-turn cost reported |
| contradicts R-coord-06 | §1 + R6 rewrite the rule and state-file.md |
| 200-char rule vs corpus | §1 fresh card; `card check` names lines |
| state-file.md two writers | Delivery: R6 only |
| heartbeat files omitted | §1 + R2 file list |
| max-lines unit | §2 records, default 30 |
| leading orphan block | §0 skipped |
| inbox without timestamps | §0 exit 1 |
| stale keyed on `→ done` only | §0 one marker list from autoos_heartbeat.py |
| question id convention | §3 `Q[-:]\s?\d*` + answered rule |
| budget arithmetic open | §3 exact bytes, ÷3 conservative |
| 8k no headroom | §3 30 records + 4k events sub-budget |
| two lane sources | §3 `l1_handoff.lanes()` only |
| pack in argv unsafe | §4 path + one-line prompt |
| prefix cache claim | §Why first request vs per-turn |
| dispatch fallthrough | R1 explicit verb table |
| 53k inbox mis-attributed | §Why corrected |
| card rollover | §1 new run seeds a new card |
| cap invariant unguarded | §5 validate asserts it |
