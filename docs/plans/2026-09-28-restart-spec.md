# RESTART — cheap relaunches: state card + context pack (spec v3.5)

Owner: autoos-L1-routing. Operator decisions D-040 (restart) and D-042 (provenance), 2026-09-28,
relayed by the L0 router.
Status: SPEC v3 (v2 Sonnet FIX-FIRST resolved, table under Delivery). v1 (0fbed20, c16ac78) got a cross-family first pass (Qwen,
`work/L1-routing/review-restart-spec.out`: 7 blockers, 11 high); v2 resolves every finding (§R at
the end). Next: the lanes (v3.5's Sonnet FIX-FIRST is resolved, table under Delivery).
v3.2 (RSTAMEND, 2026-09-28): new §7 size limits, crash recovery through the pack (§4), the L2 cap
decision (§5), and two delivery lanes (R2c, R8).
v3.3 (RSTAMEND2, 2026-09-28, Sonnet FIX-FIRST on 86da423): §7 rotate now archives only what a
covering `→ done:` names, and only under the new §0 inbox lock; §7 stops calling `status/<name>.md`
the card (§1 owns that path); §4 liveness points at the §3 snapshot; §5 takes the L2 cap to D-085's
interim 250k — superseded by D-088 in v3.5 — and opens new §8 after the fresh-session baseline behind
it (lane R9).
v3.4 (RSTAMEND3, 2026-09-28, Muse FIX-FIRST on ae6696c): §7 matches a `→ done:` to a record by that
record's **full leading stamp and its line**, and refuses to archive same-second records a done
cannot name unambiguously; §0 says *every* inbox writer takes the lock — a bare `echo … >> inbox` is
forbidden, sessions go through `inbox append` — and pins both lock timeouts (rotate 30 s / appender
10 s); §7 cites §1's card budget and §0's marker list instead of restating either; §8 pins the argv
(`--mcp-config` + `--strict-mcp-config`, verified against `claude --help`), names the three role
files and what each lists, and defines the baseline measurement exactly (lane R9).
v3.5 (RSTAMEND4, 2026-09-28, Sonnet FIX-FIRST on 2ecc900): §7 closes the hole §0's non-order list
opened — a `ready <branch> <sha>` record is a non-order, so rule (a) archived it unanswered while §3's
open-readies scan (`--since-card`, which never reads the archive) lost the lane it was waiting on; §7
now owns the one closing rule for a ready and §3 points at it. §0 names `autoos-agent.py ready` as a
writer already covered by the lock inside `append_inbox_line`, and says plainly that R8's bare-`>>`
grep only reaches shell recipes. §5 takes the L2 cap from the operator's D-088 (orchestration 500k,
workers min(40 %, 400k), lane CAPD088), which supersedes D-085's interim 250k, and §8 keeps only the
baseline work.

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
  from L1-backlog session 8e409b42's transcript): the L2 policy cap then was 150k (§5 names the cap now
  in force), and the relaunch reached 153.9k after 28 tool results / 143k chars. About 70k of
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
  every record whose (timestamp, ordinal) is > P, in file order. The ordinal is the same `<n>` §7's
  unambiguous `→ done: <full stamp>#<n>` form uses — one numbering, not two.
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
  archivability — with the one exception §7 states, that a `ready` line is not archivable until it is
  closed, because §3 is still waiting to read it.
- **Inbox lock:** every write and every rotation of `<RUN>/inbox/<name>.md` takes `flock` on
  `<RUN>/inbox/<name>.lock` — one lock, two modes. `append_inbox_line` holds it **shared** and only
  for the write (several appenders may hold it at once; the file is opened `a`, one complete line each).
  A rotation holds it **exclusive** for its whole read-write-swap, so it waits for every appender and
  no append can start inside the window. Nobody renames or truncates an inbox without holding it.
  §7's rotate cites this; the lock is not defined twice.
- **Who is a writer:** anything that appends, not only the code path. A bare
  `echo … >> <RUN>/inbox/<name>.md` is **forbidden** — it appends straight into the rotation window,
  and it is the shape a shell session reaches for first. A session appends with
  `autoos-agent.py inbox append <name> <text>` (new verb in R8), and anything that cannot call the
  tool wraps the redirect in the lock itself, with the wait cap below:
  `flock -s -w 10 <RUN>/inbox/<name>.lock -c 'printf … >> <file>'`.
  **The lock lives inside `append_inbox_line`, and that is the whole coverage story for Python**: any
  caller of it is locked the moment R1 puts the `flock` there, one implementation for all of them.
  `autoos-agent.py ready` (`cmd_ready`) is already such a writer — it appends the
  `ready <branch> <sha>` line through `append_inbox_line` — so it needs no lane of its own, and R8's
  `inbox append` verb joins it rather than adding a second path. It also means a *new* Python writer
  must call `append_inbox_line` instead of opening the file itself.
  R8's test greps the tracked skill and brief files for a bare `>> …/inbox/` and fails naming file and
  line — that grep covers **shell recipes only**: a `.py` that writes the inbox directly is invisible
  to a text search for `>>`, which is why the coverage above is stated as "call
  `append_inbox_line`" and not as "the grep will catch it". R6 — SKILL.md's only writer — lands the
  rule line that cites this bullet.
- **No deadlock, bounded waits:** one lock per inbox, and it is never upgraded — an appender that
  holds shared never asks the same lock for exclusive, so no holder waits on something it already
  holds, and with one lock resource per inbox there is no cycle to form. Both waits are capped. A
  rotation acquires exclusive with a **30 s** timeout (`flock -w 30`) and on timeout exits 2 naming
  the inbox and moving nothing — heartbeat retries it on the next beat. An appender waits at most
  **10 s**, then appends anyway and reports `inbox: lock timeout`: an append is never refused, so the
  escape valve trades the lock's guard for liveness rather than losing a line. The residual window
  that guard closes — a line landing on the pre-swap inode between rotate's re-read and its rename —
  therefore only exists when a rotation has already been wedged for 10 s, which the report names for
  the next beat to see.
- **Concurrent writers:** several sessions append to one inbox. A reader ignores a final line that
  has no trailing newline (a torn append): it is not a record and not a continuation, and the next
  read sees it whole. Writers append one complete line per write (`append_inbox_line` already does,
  in `a` mode). A record whose timestamp line fails the ISO parse *after* a newline is reported as
  `malformed at line N`, never glued onto the previous record. What keeps an append and a rotation
  from racing each other is the Inbox lock and its two writer bullets above; the torn-line rule only
  describes what a reader sees.

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
   - An **open ready** is a `ready <branch> <sha>` record — the line a session appends into its
     parent's inbox (§0's `cmd_ready`) — that nothing has closed yet, and **§7 owns the closing rule**:
     it states both closers in one place, because rotate must apply exactly the same test before it may
     archive that line. This scan sees readies through `--since-card`, which §7's Reads rule confines to
     the live file, so an unclosed ready that rotate had moved would disappear from every later pack
     and the parent's successor would never merge the lane. Read §7's `ready` bullet for what closes
     one; the closer is stated once, there.
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

- The facts come from `<RUN>/run.json` (git-ignored, per run): for each session its name, its `role`
  (one of §8's three role-file stems — it is what selects the session's MCP config file), its
  worktree, model, MCP config path and parent. The tracked example is `configuration/run.example.json`, with
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
- **L2 cap: operator D-088 (2026-09-28)** — orchestration sessions **500k**, workers **min(40 % of
  window, 400k)**, in `policy.handoff_caps` (lane **CAPD088**); supersedes D-085/CAPL2's interim 250k.
  Only §8's baseline work remains. What D-085 measured is why a cap is a number at all and stands
  untouched: L1-backlog was handing off every ~15 min against a fresh sonnet L2 start cost of **55.5k**
  (system prompt + MCP tools + CLAUDE.md, session 967be39d's first turn), so the handoffs, not the work,
  were what cost. That 55.5k is §8's pre-R9 anchor, not a cap. The cap numbers live in the registry,
  never in prose, and CAPD088 inherits three obligations from the row format.
  - **The split is per role, and `cap_for` is per model today.** `tools/autoos_context.py` `cap_for`
    takes a model id and no role, so the two D-088 numbers cannot both come from one row: CAPD088 keys
    rows by (family, role), reads the role from §4's run.json `role` field (§8's three stems —
    `l2-orchestrator` is an orchestration session, `l3-worker` and `l3-reviewer` are workers), and only
    lets the role select among rows that *name* a role — a family with a single row resolves exactly as
    it does today. That keeps D-088's scope to what it decided: these are the L2 numbers, and the
    `claude-opus-1m` row above stays D-044's, not reopened here. A caller with no role to name takes the
    orchestration row, so an existing `context`/`heartbeat` call never changes meaning.
  - **Order.** `cap_for` matches by substring and the FIRST row wins, and a trailing `[1m]` is stripped
    before matching, so every new row goes **before** the `*` 200k-class row — appended after it, the
    model silently keeps matching `*`. A test pins each role's id resolving to its own number and not to
    the wildcard.
  - **The invariant.** `tools/registry.py validate` asserts `cap_tokens == window × cap_fraction`, and a
    `min()` cap satisfies it only where the written-down `cap_fraction` is the ratio that reproduces the
    token number (1M-window worker: 400000 / 0.4; a 200k-class worker: 80000 / 0.4). Where the `min()`
    binds, `cap_fraction` records the resulting ratio, and `DEFAULT_CAPS` mirrors the same rows in the
    same order.
  - **Revisit.** §8's protocol is what measures what a session pays to start; its before/after numbers
    go to the L0 router, and revisiting D-088 is the operator's call — this spec sets no trigger for it
    (v3.3's "lower it again once a relaunch costs < ~20k" went with the row it gated).
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
  would be a second home for the same facts. Until R6 merges, it still exists and carries **the §1
  card budget** — §1 owns both numbers (the line cap and the per-line character cap), so they are not
  repeated here — and `card check` takes a path and runs on whichever of the two files exists, so the
  cap is code-checked, not prose (lane R2a; R6 deletes the file and the second call site with it).
- **Inbox rotation:** new verb `autoos-agent.py inbox rotate <name>|--file PATH`.
  - Trigger: the inbox file exceeds 50 KB, or its first record is from an earlier UTC month than today.
  - **Archivable** — this is what "acknowledged" means, and position alone is not enough of it. A
    record moves only if either: (a) it is **not an order**, by the whole §0 `_NOT_AN_ORDER_RE` list
    R2a extends — §0 names the shapes, and such a line records what already happened, so nothing
    downstream waits on it; or (b) it **is** an order and a later `→ done:` line **names it**.
    Anything else is an open order and stays live **whatever its position**. (why: with position as
    the only test, an unrelated `→ done:` that happened to follow a PAUSE archived the PAUSE and the
    successor never saw the order again.)
  - **The `ready` exception — a non-order that something still waits on** (this is the ONE home of the
    rule; §3 points here). Rule (a) is not enough for a `ready <branch> <sha>` record. §0 classes it as
    a non-order because it answers no one, but the parent's pack lists it as an open ready from
    `--since-card`, and this section's Reads rule confines that mode to the live file — so archiving an
    **unanswered** ready does not tidy up history, it deletes the only remaining reminder that a lane is
    unmerged. A ready is therefore archivable only once **closed**, and closed means a later record in
    the same inbox that is either: (i) a `→ done:` that names it exactly as (b) names an order, or (ii)
    a line pairing `main=` with the first 7 hex of the ready's `<sha>` — the merge answer, matched
    against a record's own text, never a timestamp quoted in a body. Until one of those exists the ready
    is treated as an open order: it stays live **whatever its position**, and rotate counts it in
    `kept N records: no covering done` — "done" there names either closer.
  - **"Names it", exactly** — a record's address is its **full leading stamp**
    `YYYY-MM-DDTHH:MM:SSZ` **plus its line number**. A `→ done:` closes it only if all of:
    - the done line is at a **greater line number** than the record's stamp line — file order, which
      is how §0 reads positions too, so a clock-skewed earlier stamp is never "later";
    - it names that record's **full** stamp, or its short `HH:MM:SS` **only** when exactly one
      not-yet-closed record on an earlier line carries that short time (the
      `→ done: 12:12:24 <what shipped>` form real inboxes use);
    - the match is against the record's **leading** stamp: a timestamp in a record's body — a
      continuation line, a quoted inbox line, a sha that reads like a time — is not an address and
      never closes anything.
    One done line may name several records, each matched the same way. Because §0 measures 81
    same-second collisions in one inbox, a full stamp can be carried by more than one record: when a
    stamp matches more than one still-open record, **none of them is archivable** until a done names
    one unambiguously, and the unambiguous form is `→ done: <full stamp>#<n>` — `<n>` is §0's
    ordinal, the n-th record with that exact timestamp in file order, so the done form reuses the
    position §0 defines rather than inventing a second numbering. Ambiguity is reported, not
    resolved by guessing.
  - Moves: every archivable record at or before the card's `last-event` position (§0), into
    `<RUN>/inbox/archive/<name>-<YYYYMM>.md`, filed under the record's own month.
  - Stays: everything after the card's position, every record that is not archivable, and the
    `# inbox …` header. Rotate prints two counts, never one merged number:
    `kept N records: no covering done` — the open orders no later `→ done:` named — and
    `kept N records: ambiguous stamp` — the records a done could only match ambiguously.
  - Swap, under the §0 inbox lock held **exclusive** for the whole operation: re-read the live file
    under the lock (never decide from a read taken before it), append the moved records to the
    archive, write the new live file to a tmp in the same directory, `fsync` both, rename the tmp over
    the inbox, release. Acquisition runs on §0's 30 s timeout: on timeout rotate exits 2 having moved
    nothing, and heartbeat retries it on the next beat. The §0 append rules hold across the swap: a
    torn last line stays a torn last line and is never glued onto a record, and an append cannot be
    swallowed by the rename because an appender holds the shared lock the rotation waits for (the one
    exception is §0's 10 s appender escape valve, which reports itself).
  - Idempotent: a second rotate moves nothing.
  - `heartbeat` runs it at most once per hour per inbox.
  - Reads: `inbox --all` reads the archive then the live file; `--since-card` never opens the archive.
  Lane R8.
- **Prompts and skill rules:** no relaunch prompt and no skill rule tells a session to read an inbox,
  handoff, status, brief or log whole. A log is read with `tail -n 40` or a grep. The only prompt a
  relaunch gets is §4's one line; R6 writes the rule.

## 8. Fresh-session baseline (the problem D-085 named, 2026-09-28; the cap that rode it out is D-088's)

§5's L2 cap is set high to *ride out* the baseline (D-088: 500k for orchestration sessions). The
baseline is what a fresh session pays before it reads anything of its own — for a sonnet L2, the
D-085 sample measures 55.5k of system prompt + MCP tool schemas + CLAUDE.md. That sample is the
anchor, not the method: the number that
decides before/after is defined by the protocol in the last bullet below. The system prompt is not
ours; the other two are, and both are cut per role, not per model.

- **A lean MCP set per role, and the exact argv that loads it.** The mechanism is pinned, not
  sketched: `claude --mcp-config configuration/mcp/<role>.json --strict-mcp-config`. Both flags were
  verified against `claude --help` in this sandbox on 2026-09-28 (CLI 2.1.283): `--mcp-config
  <configs...>` loads servers "from JSON files or strings (space-separated)" and
  `--strict-mcp-config` uses "only MCP servers from --mcp-config, ignoring all other MCP
  configurations" — so no user-scope or project-scope server can be added back, and a file whose
  `mcpServers` object is empty (`{"mcpServers": {}}`) means **no MCP servers at all** for that
  session. The pair is already the repo's idiom, not a new one: `tools/autoos_clients.py` builds
  `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` for joinable runs, and
  `trust_worktree.py --lane-mcp` writes a `.claude/lane-mcp.local.json` loaded the same way.
  `--lean` does the blunt half today — for a client in `MCP_STRICT_CLIENTS` it inserts a bare
  `--strict-mcp-config` with no `--mcp-config`, i.e. zero MCP servers — and the role documents are the
  selective half, so an orchestrator keeps the two servers it needs while a worker gets the empty
  document. The pair is claude/qoder only — `tools/autoos-agent.py` `MCP_STRICT_CLIENTS` is
  `("claude", "qoder")`; qwen, gemini and codex run behind `omniroute run <target>`, which would take
  the flag as its own and reject it, and opencode cuts servers (`LEAN_DROP`) through its config
  overlay instead of argv.
- **The role files, and exactly what each lists.** R9 creates `configuration/mcp/` with these three
  tracked `.example` templates and no others — fake values only, like §4's `run.example.json`, since
  a real one carries machine paths AGENTS.md rule 1 forbids here. The argv above points at the
  un-suffixed runtime document `configuration/mcp/<role>.json`, generated from its template and
  git-ignored the way `run.json` is (R9 adds the ignore rule):
  - `configuration/mcp/l2-orchestrator.json.example` — `autoos-agent` + `omnigraph`, nothing else
    (an orchestrator's job is spawning, reviewing and memory);
  - `configuration/mcp/l3-worker.json.example` — `{"mcpServers": {}}`: empty, so a leaf role's
    `autoos-agent` tools are *absent*, not merely unused (R-worker-06). "Nothing beyond the client's
    own defaults" is not expressible under `--strict-mcp-config` — the file **is** the whole set —
    which is why the worker's file is the empty document rather than a short list;
  - `configuration/mcp/l3-reviewer.json.example` — `{"mcpServers": {}}`: the same empty document,
    a reviewer reads the diff and reports, and needs no server to do it.
  `<role>` is §4's run.json `role` field, and the three names above are its whole domain.
  `relaunch-line` and `run --client claude` pass the flag pair, taking the file for the session's
  role from that field — §4's field is the only place a role's config path is written down. A role
  with no file is an error naming the role: silently starting a worker with no MCP because the role
  string did not match is the failure mode this pins shut, not a default.
- **A trimmed always-loaded set.** CLAUDE.md / AGENTS.md keeps only what *every* session must read
  (the hard rules, the entry points, the definition of done); everything else moves behind a pointer
  to the skill or reference file that owns it. One home per fact decides what stays: a fact whose home
  is elsewhere is a pointer there, not a copy here.
- **The baseline, defined so before and after are the same measurement.** A role's baseline is the
  **first assistant turn** of a fresh session: that usage record's `input_tokens +
  cache_creation_input_tokens + cache_read_input_tokens`, read from the transcript with the same
  all-records iterator §5's `token-rate` uses (so it needs no gateway). It is **unweighted** — no
  `CACHE_READ_WEIGHT`, and `output_tokens` is excluded: this measures how much context a session paid
  to *start*, not what it cost to run — and **non-sidechain** (`isSidechain` false; a subagent's turn
  is not the session's start). The protocol is fixed: the probe prompt `Reply OK` and nothing else as
  the task, the role's pinned model and that role's config file, **3 runs**, and the **median** — one
  run is cache-state noise, three is the cheapest set with a middle.
  `tools/autoos_tokenrate.py` gains a `baseline` measurement and
  `autoos-agent.py token-rate --baseline [--role <name>]` prints it per role; both numbers — before,
  after the trim — go to the L0 router with the role and the pinned model named. The 55.5k in §5
  (D-085, session 967be39d's first turn) is cited as **the pre-R9 anchor only**: a real session's
  first turn, not a protocol run. Where R9's "before" differs from it, the protocol number stands
  and the difference is reported, not smoothed into either. Lane R9.

## Delivery (each lane test-first; cheap writer → cross-family review → Sonnet final)

| lane | scope | files (one writer each) |
|---|---|---|
| R1 inbox | §0 module + `inbox`; the §0 inbox lock on the append side (`append_inbox_line`, shared); replace `main()`'s dispatch fallthrough (`cmd_list … else cmd_run`) with an explicit verb table so later verbs cannot fall into `run` | tools/autoos_inbox.py (new), tools/autoos-agent.py (dispatch + inbox verb), tests |
| R2a card | `card check` + extend `_NOT_AN_ORDER_RE` (the ready/pong/info shapes too, §7); `card check` takes a path so it also caps `status/<name>.md` until R6 retires it | tools/autoos-agent.py, tools/autoos_heartbeat.py, tests |
| R2b stale | heartbeat `card: stale`: `heartbeat_state` param, JSON key tuple, MCP twin | tools/autoos-agent.py, tools/autoos_agent_mcp.py, tests |
| R2c handoff cap | §7 `l1_handoff.py` budget (refuse over 5k, name the section), line dedupe, no embedded status file | .agents/skills/unattended-orchestration/l1_handoff.py, tests |
| R8 rotate | §7 `inbox rotate` verb: archivable = non-order (§0 list) or named by a later `→ done:` at a **greater line number** (full stamp, or a short stamp only while it is unique among open records; `#<n>` = §0's ordinal), **plus §7's `ready` exception** (a `ready` line is archivable only once §7's closing rule has closed it, and rotate counts an unclosed one in the `no covering done` total), the two `kept N:` counts, exclusive §0 lock + re-read, tmp/`fsync`/rename swap, §0's 30 s rotate / 10 s appender timeouts, heartbeat hook (≤1/hour/inbox), archive read rules; plus §0's `inbox append` verb (calls `append_inbox_line`, so the shared lock keeps one implementation, joining the existing `ready` caller) and the test grepping tracked skill/brief files for a bare `>> …/inbox/` — shell recipes only | tools/autoos_inbox.py, tools/autoos-agent.py, tests |
| R3 pack | `pack`, budget, `l1_handoff.py --pack` | tools/autoos-agent.py, .agents/skills/unattended-orchestration/l1_handoff.py, tests |
| R7 provenance | §6 store, manifests, events, `gen=`, spawn manifests, card versions, prune, replay/diff | tools/autoos_context_store.py (new), tools/autoos-agent.py, tests |
| R4 relaunch | `relaunch-line`, run.json schema + example (its `role` field is §8's stems — R4 owns the field, R9 consumes it) | tools/autoos-agent.py, configuration/run.example.json (new), tests |
| R5a metric | `token-rate` verb + all-records iterator; the before-number | tools/autoos_tokenrate.py (new), tools/autoos-agent.py, tests |
| R5b cap | shared opus/fable row 350k (D-044), DEFAULT_CAPS + pinned tests, validate invariant | catalog/ai-registry.json, tools/autoos_context.py, tools/registry.py, tests |
| CAPL2 (retired, 2026-09-28) | §5's interim `claude-sonnet-1m` 250k row, superseded by D-088 before it was built — the lane number is not reused. CAPD088 carries the ordering and pinned-test obligations named there | — |
| CAPD088 | §5 D-088 numbers in `policy.handoff_caps`: orchestration 500k, workers min(40 % of window, 400k); the (family, role) row key + `cap_for`'s role argument read from R4's `role` field, a single-row family resolving unchanged; every new row **before** the `*` row, `cap_fraction` the ratio that reproduces `cap_tokens` so `validate`'s invariant holds, + the same rows in the same order in DEFAULT_CAPS; tests pin each role resolving to its own number, not the wildcard | catalog/ai-registry.json, tools/autoos_context.py, tests |
| R6 skill | R-coord-06/08 text; `references/state-file.md` becomes the card spec (its only writer); the stale `briefs/common.md` rule sources; the rule line telling a session to append with §0's `inbox append` (§0 owns the fact, R6 only words the rule) | SKILL.md, references/ |
| R9 baseline | §8: the three named strict `--mcp-config` role documents, the pinned `--mcp-config … --strict-mcp-config` argv (claude/qoder only, `MCP_STRICT_CLIENTS`) passed by `relaunch-line` and `run --client claude` from R4's `role` field, the always-loaded trim, and the `baseline` protocol (probe `Reply OK`, pinned model + role document, 3 runs, median, non-sidechain first assistant turn) | configuration/mcp/ — `l2-orchestrator.json.example`, `l3-worker.json.example`, `l3-reviewer.json.example` (new, plus the ignore rule for the un-suffixed runtime documents), tools/autoos-agent.py (relaunch-line, run), tools/autoos_tokenrate.py, AGENTS.md, CLAUDE.md, tests |

Order: R1 first (it freezes the dispatch). Then R2a and R5a in parallel (different files except one
dispatch-table line each), then R2b. Then R2c and R8, right after R2b and before R3. Then R3, R7 (after
REDACTMERGE is on main), R4, R5b (after the before-number), CAPD088 (after R5b — the same registry and
fallback files, one writer at a time — and after R4, since it reads R4's `role` field), R9 (after R4,
since it adds a flag to `relaunch-line`, and after R5a, since it extends the token-rate module), and
R6. Every lane after R1 merges main
before it starts. Each lane is at most three items per worker run.

Two cross-lane details are decided here so no lane discovers them mid-run: the `role` field §4's
run.json carries is R4's (R9 consumes it and never edits the schema or its example), and R8's
bare-append grep reads the whole tracked skill and brief corpus — if it names a recipe line in a file
another lane owns (SKILL.md is R6's), R8 fixes that one line and nothing else in the file.

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
| HIGH: rotate's "acknowledged" undefined; position alone archives an open PAUSE/order when an unrelated `→ done:` follows | §7 defines archivable — non-order by the §0 list, or an order a later `→ done:` names by its leading UTC timestamp (v3.4 turns that into a matching rule: full stamp + line number, see the table below); everything else stays live whatever its position, and rotate prints the kept count |
| HIGH: rotate's read-to-rename window drops a concurrent §0 append | §0 inbox lock (`<inbox>.lock`): shared and short on every append, exclusive across rotate's re-read → tmp → `fsync` → rename; §7 points at it |
| HIGH: §7 called `status/<name>.md` "the card" while §1 fixes the card at `status/<name>.card.md` | §7 keeps one path (§1's) and says the free-form file is retired when R6 lands; until then it carries the §1 cap |
| MEDIUM: §4 sent liveness to §3 item 6 and carried its sources there | Liveness is the §3 item 2 snapshot line, sources named once there; §4 only says it never goes into a prompt |
| L2 cap 150k vs the measured trigger | §5 D-085: trigger met (handoff every ~15 min, 55.5k fresh sonnet baseline), new `claude-sonnet-1m` row at 250k, lane CAPL2 (v3.5: D-088 supersedes that interim number and CAPL2 is retired unbuilt — §5 carries the cap now, and the 55.5k stays as §8's anchor) |
| what the cap was riding out | new §8: lean per-role `--mcp-config` + trimmed always-loaded set, measured before/after per role, lane R9 |

| v3.3 Muse finding (on ae6696c) | v3.4 resolution |
|---|---|
| HIGH: "a later `→ done:` names that record's leading UTC timestamp" is a rule that cannot be implemented — §0 measures 81 same-second collisions, and a stamp quoted inside a body line would close the wrong record | §7 states the address and the three tests: a record is its **full leading stamp plus its line number**, the done must be at a greater line number, it must name the full stamp (or a short `HH:MM:SS` only while exactly one open earlier record carries it), and only a leading stamp is an address. A stamp matching several open records archives none until `→ done: <full stamp>#<n>` (§0's ordinal) names one; rotate prints `kept N: ambiguous stamp` beside the `no covering done` count |
| HIGH: §0's lock named "every write" but only `append_inbox_line` held it — a skill or brief recipe saying `echo … >> inbox` loses that line to a rename | §0 says who a writer is and forbids the bare redirect: `inbox append` (new R8 verb, same code path as `append_inbox_line`, so the lock has one implementation) or `flock -s`. R8 greps the tracked skill and brief files for the bare form, R6 words the rule line, and §0 states why one lock per inbox with no shared→exclusive upgrade cannot deadlock |
| MEDIUM: no wait was bounded anywhere — one wedged rotation could hold every appender and every heartbeat behind it | §0 caps both directions: rotate acquires exclusive with a 30 s timeout and on timeout exits 2 having moved nothing (heartbeat retries next beat); an appender waits 10 s, then appends anyway and reports `inbox: lock timeout`, so the guard gives way before a line can be lost to it |
| MEDIUM: §7 restated §1's 40-line / 200-char budget and §0's marker shapes, so the same facts had three homes and could drift apart | §7 cites §1 for the budget and §0 for the marker list, naming neither numbers nor shapes of its own |
| MEDIUM: §8 said "a strict `--mcp-config` file" without the argv, without the empty-document form, without saying which role files exist or what each lists | §8 pins `claude --mcp-config configuration/mcp/<role>.json --strict-mcp-config` (both flags checked against `claude --help`, CLI 2.1.283), names the three role documents and exactly what each holds, points at the code already using the pair, limits it to `MCP_STRICT_CLIENTS`, and says which verbs pass it from §4's `role` field — an unknown role is an error, not a silent no-tools session |
| MEDIUM: "the baseline is one fresh session's first-turn input tokens" is not a measurement, so before/after would not be comparable and 55.5k not reproducible | §8 defines it: the non-sidechain **first assistant turn's** `input + cache_creation + cache_read`, unweighted and output-free, on the fixed probe prompt `Reply OK` with a pinned model and the role's document, 3 runs, the median, reported per role to L0. §5's 55.5k is the pre-R9 anchor only |

| v3.4 Sonnet finding (on 2ecc900) | v3.5 resolution |
|---|---|
| HIGH: §0 classes a `ready` line as a non-order, so §7's rule (a) archives it **unanswered** — and §3's open-readies scan reads `--since-card`, which never opens the archive, so the successor loses the only reminder that a lane is unmerged | §7 adds the `ready` exception as the rule's one home: a ready is archivable only once a later record in the same inbox closes it, either a `→ done:` naming it exactly as rule (b) names an order or a line pairing `main=` with the first 7 hex of its `<sha>`; until then it is an open order, stays live whatever its position, and rotate counts it in `kept N records: no covering done`. §3 and §0's marker bullet point at §7 instead of defining a closer, §R's "open readies" row follows the move, and the R8 lane scope names the exception so it is built, not discovered |
| MEDIUM: §0 named only the two future appenders, so a reader could not see that the `ready` line already in every real inbox is written by a locked path — nor that the grep would never catch an unlocked Python writer | §0 states where the lock lives (`append_inbox_line`) and names `autoos-agent.py ready` (`cmd_ready`) as a covered caller that needs no lane of its own; it says plainly that R8's bare-`>>` grep reaches **shell recipes only**, and that a new Python writer's obligation is to call `append_inbox_line`, not to survive a text search |
| §5 still presented D-085's interim 250k as the L2 cap | §5 leads with operator D-088 (orchestration 500k, workers min(40 % of window, 400k), lane CAPD088) and keeps only what is still true from D-085 — the ~15 min handoff and 55.5k measurements, which are §8's anchor. CAPL2 is retired unbuilt, the ordering trap moves to CAPD088 with the (family, role) keying `cap_for` needs, §8's lead and the v3.3 table row stop naming 150k/250k as current, and only §8's baseline work is left open |

## §R. v1 review findings → v2 resolution

| finding | resolution |
|---|---|
| last-event not unique (81 collisions) | §0 position = timestamp#ordinal, at-least-once |
| `inbox <name>` has no name→path | §0 `$AUTOOS_RUN_DIR` or `--file` |
| `usage` is gateway-only | §5 new verb `token-rate` |
| `fill_from_transcript` keeps one record | §5 new iterator over all records |
| no role brief file in the skill | §3 prefix = rules + main-orchestrator.md; run brief is variable |
| `.claude/handoff.config.json` is tracked and stale | §4 git-ignored `<RUN>/run.json` + tracked example |
| open readies unmatched | §7's closing rule for a ready (a `→ done:` naming it, or a line pairing `main=` with the sha prefix); §3's scan points at it |
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
