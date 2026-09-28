# RESTART — cheap relaunches: state card + context pack (spec v3)

Owner: autoos-L1-routing. Operator decisions D-040 (restart) and D-042 (provenance), 2026-09-28,
relayed by the L0 router.
Status: SPEC v3 (v2 Sonnet FIX-FIRST resolved, table under Delivery). v1 (0fbed20, c16ac78) got a cross-family first pass (Qwen,
`work/L1-routing/review-restart-spec.out`: 7 blockers, 11 high); v2 resolves every finding (§R at
the end). Next: the Sonnet review, then the lanes.

## Why (measured)

- A relaunch reads 14–17k tokens when it reads only the inbox tail, and 40–70k when it re-reads
  whole inboxes (L0, 2026-09-28). The largest inbox is `inbox/L1-routing.md`: 214,669 B ≈ 53.7k
  tokens. An L2-only inbox is ~7k.
- 20+ orchestrator relaunches in 48 h.
- On this host, 99.2 % of the orchestrator tokens in 48 h were cache reads (4.45 B of 4.48 B,
  16,138 turns). So the per-turn cost grows with context size: a turn at 600k re-reads 600k.
  The byte-identical prefix pays off *within* a session (every later turn reads it from cache). A
  successor's first request pays the full input once, and that is what the ≤8k variable part keeps small.

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
  `→ relaunched`, `→ operator`, `→ main`) with a test per marker. §1 and §3 cite that one list.
- **Concurrent writers:** several sessions append to one inbox. A reader ignores a final line that
  has no trailing newline (a torn append): it is not a record and not a continuation, and the next
  read sees it whole. Writers append one complete line per write (`append_inbox_line` already does,
  in `a` mode). A record whose timestamp line fails the ISO parse *after* a newline is reported as
  `malformed at line N`, never glued onto the previous record.

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
  (§0) written by this session: the session acted on something its card does not absorb. That needs
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
   and its `lanes()`, the ONE lane source); live spawner workers (`ps`); open readies; open questions.
   - An **open ready** is a `ready <branch> <sha>` record this session wrote into its parent's inbox
     for which no later record in its own inbox from the parent contains the first 7 hex of `<sha>`
     together with `main=`.
   - An **open question** is a record in `<RUN>/inbox/L0.md` or this session's inbox matching
     `Q[-:]\s?\d*` or `question:` that was written by this session and is not answered. Answered
     means a later record matches `\banswer\s+(?P<id>Q-\d+)\b` or `\b(?P<id>Q-\d+)\s+ANSWERED\b`
     (the id-first form in use) with the same id. A `question:` without an id is open until a later
     `→ done` record of this session quotes its first 40 characters.
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

## 5. Context cap and the metric

- **Cap:** per orchestrator role, not per model (router D-044): the shared registry row
  `claude-opus-1m` (`["opus","fable"]`) goes to `cap_tokens` 350000 / `cap_fraction` 0.35 (source
  D-040, D-044). If Fable's before/after numbers show a quality loss, the row is split then. `tools/autoos_context.py` `DEFAULT_CAPS` (the unreadable-registry fallback) and its
  pinned tests change in the same lane. `tools/registry.py validate` asserts
  `cap_tokens == window × cap_fraction`.
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

- **Redaction:** every blob is written through the repo's one secret-pattern module
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

## Delivery (each lane test-first; cheap writer → cross-family review → Sonnet final)

| lane | scope | files (one writer each) |
|---|---|---|
| R1 inbox | §0 module + `inbox`; replace `main()`'s dispatch fallthrough (`cmd_list … else cmd_run`) with an explicit verb table so later verbs cannot fall into `run` | tools/autoos_inbox.py (new), tools/autoos-agent.py (dispatch + inbox verb), tests |
| R2a card | `card check` + extend `_NOT_AN_ORDER_RE` | tools/autoos-agent.py, tools/autoos_heartbeat.py, tests |
| R2b stale | heartbeat `card: stale`: `heartbeat_state` param, JSON key tuple, MCP twin | tools/autoos-agent.py, tools/autoos_agent_mcp.py, tests |
| R3 pack | `pack`, budget, `l1_handoff.py --pack` | tools/autoos-agent.py, .agents/skills/unattended-orchestration/l1_handoff.py, tests |
| R7 provenance | §6 store, manifests, events, `gen=`, spawn manifests, card versions, prune, replay/diff | tools/autoos_context_store.py (new), tools/autoos-agent.py, tests |
| R4 relaunch | `relaunch-line`, run.json schema + example | tools/autoos-agent.py, configuration/run.example.json (new), tests |
| R5a metric | `token-rate` verb + all-records iterator; the before-number | tools/autoos_tokenrate.py (new), tools/autoos-agent.py, tests |
| R5b cap | shared opus/fable row 350k (D-044), DEFAULT_CAPS + pinned tests, validate invariant | catalog/ai-registry.json, tools/autoos_context.py, tools/registry.py, tests |
| R6 skill | R-coord-06/08 text; `references/state-file.md` becomes the card spec (its only writer); the stale `briefs/common.md` rule sources | SKILL.md, references/ |

Order: R1 first (it freezes the dispatch). Then R2a and R5a in parallel (different files except one
dispatch-table line each), then R2b. Then R3, R7 (after REDACTMERGE is on main), R4, R5b (after the
before-number), and R6. Every lane after R1 merges main before it starts. Each lane is at most three
items per worker run.

| v2 Sonnet finding | v3 resolution |
|---|---|
| blobs store raw inbox/brief text | §6 Redaction through autoos_redact; R7 after REDACTMERGE |
| opus row also matches fable | §5 router D-044: cap is per role, row stays shared, both 350k |
| `_NOT_AN_ORDER_RE` is only `lesson:\|→ done` | §0 R2a extends it, test per marker |
| torn appends by concurrent writers | §0 last line without newline ignored; malformed reported |
| R2/R5 over 3 items | R2a/R2b, R5a/R5b |
| canonical JSON undefined | §6 pinned serialization + id test |
| 90-day prune vs replay | §6 trade-off stated in the goal |
| "answered" left to prose | §3 exact patterns |

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
